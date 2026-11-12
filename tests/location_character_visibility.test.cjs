const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const scene = fs.readFileSync(path.join(__dirname, '../app/static/js/locationScene.js'), 'utf8');
const style = fs.readFileSync(path.join(__dirname, '../app/static/style.css'), 'utf8');

function section(start, end) {
    const first = scene.indexOf(start);
    const last = scene.indexOf(end, first);
    assert.ok(first >= 0 && last > first);
    return scene.slice(first, last).replaceAll('export ', '');
}

test('осмотр использует только точку ГМа, а не обзор размещённых союзников',()=>{
    const context=vm.createContext({window:{isGM:false,locationScoutView:{check:{success:true},point:{x:4,y:5,floor_level:2,facing_x:1,facing_y:0}}},
        STOREY_HEIGHT:2.4,getTileHeight:()=>1,characterModels:new Map([[1,{posX:20,posY:20}]])});
    vm.runInContext(section('function getFogVisionSources(', 'function blocksVision('),context);
    const sources=context.getFogVisionSources();
    assert.equal(sources.length,1);assert.equal(sources[0].posX,4);assert.equal(sources[0].floorLevel,2);assert.equal(sources[0].physicalHeight,5.8);
    context.window.locationScoutView.check.success=false;
    assert.equal(context.getFogVisionSources().length,0);
});

test('осмотр ГМа тоже включает туман и не изменяет постоянную память карты',()=>{
    const context=vm.createContext({window:{isGM:true,locationScoutView:{point:{x:1,y:1}}},currentLocationData:{},
        localStorage:{getItem:()=>1},gmVisionCharacterId:null});
    vm.runInContext(section('function fogIsActive(', 'function toggleGmVision('),context);
    vm.runInContext(section('function getFogMemoryStorageKey(', 'function loadFogMemory('),context);
    assert.equal(context.fogIsActive(),true);assert.equal(context.getFogMemoryStorageKey(),null);
});

test('Огненная слеза обходит темноту только для своего наблюдателя и сохраняет преграды', () => {
    let open = true;
    const context = vm.createContext({ firstPersonView: null, window: { isGM: false }, gmVisionCharacterId: null, getCurrentUserId: () => 1,
        FOG_VIEW_RADIUS: 25, FOG_VIEW_HALF_ANGLE: Math.PI / 3, darknessFactorAt: () => .07,
        visionEyeHeight: () => 1.7, hasLineOfSight: () => open });
    vm.runInContext(section('function sourceHasArtifactDaylightVision(', 'function syncArtifactDaylightVision('), context);
    vm.runInContext(section('function isPointVisibleToPlayer(', 'function isTileVisibleToPlayer('), context);
    const own = { characterId: 10, characterOwnerId: 1, artifactDaylightVision: true, posX: 0, posY: 0, facingX: 1, facingY: 0 };
    const ally = { ...own, characterId: 11, characterOwnerId: 2 };
    assert.equal(context.isPointVisibleToPlayer(2, 0, 1.7, [own]), true);
    assert.equal(context.isPointVisibleToPlayer(2, 0, 1.7, [ally]), false);
    assert.equal(context.isPointVisibleToPlayer(-2, 0, 1.7, [own]), false);
    open = false;
    assert.equal(context.isPointVisibleToPlayer(2, 0, 1.7, [own]), false);
});

test('GM-hidden and camouflaged models restore their original appearance independently', () => {
    const material = { opacity: 1, clone() { return { ...this }; }, dispose() {} };
    const child = { isMesh: true, material, castShadow: true, receiveShadow: true };
    const entry = {
        model: { userData: {}, traverse(callback) { callback(child); } },
        label: { element: { style: {} } },
        gmHidden: false, camouflaged: false,
    };
    let refreshes = 0;
    const context = vm.createContext({scoutedCharacters:new Set(),
        window: { isGM: true },
        getCharacterModelEntry: () => entry,
        makeGmHiddenGhost(model) { model.userData.gmHiddenGhost = true; child.material.opacity = 0.38; },
        tintLocationMesh() {}, darknessFactorAt: () => 1,
        applyFogOfWar() { refreshes++; },
        renderCombatHud() {},
        combatState: null,
    });
    vm.runInContext(section('function applyCharacterVisibilityVisual(', 'export function updateCharacterPosition('), context);
    context.applyCharacterVisibilityVisual(1, { gmHidden: true });
    assert.notEqual(child.material, material);
    assert.equal(child.material.opacity, 0.38);
    context.applyCharacterVisibilityVisual(1, { camouflaged: true, gmHidden: false });
    assert.notEqual(child.material, material);
    context.applyCharacterVisibilityVisual(1, { camouflaged: false });
    assert.equal(child.material, material);
    assert.equal(child.castShadow, true);
    assert.equal(entry.label.element.style.opacity, '1');
    assert.equal(refreshes, 3);
});

test('hidden or camouflaged characters are not visible through fog memory', () => {
    const context = vm.createContext({firstPersonView:null,scoutedCharacters:new Set(),
        isPointVisibleToPlayer: () => true,
        getTileHeight: () => 1,
    });
    vm.runInContext(section('function isCharacterVisibleToPlayer(', 'function isFogHiddenObject('), context);
    assert.equal(context.isCharacterVisibleToPlayer({ posX: 1, posY: 1, gmHidden: true }, []), false);
    assert.equal(context.isCharacterVisibleToPlayer({ posX: 1, posY: 1, camouflaged: true }, []), false);
    assert.equal(context.isCharacterVisibleToPlayer({ posX: 1, posY: 1 }, []), true);
    assert.match(scene, /if \(current\?\.gmHidden \|\| current\?\.camouflaged\) return false/);
    assert.match(scene, /if \(current\?\.gmHidden \|\| current\?\.camouflaged\) return;/);
});

test('location labels keep a dark surface while owner and team colors stay narrow accents', () => {
    const entry = { teamBadge: { style: {}, textContent: '' }, ownerId: 7,
        model: { traverse() {} }, label: { element: { style: {
            backgroundColor: 'rgba(17, 23, 20, 0.84)',
            setProperty(name, value) { this[name] = value; },
        } } } };
    const context = vm.createContext({scoutedCharacters:new Set(),
        getCharacterModelEntry: () => entry,
        characterModels: new Map([[1, entry]]),
        getUserColorHex: () => '#ff0000',
    });
    vm.runInContext(section('function normalizeTeamColor(', 'export function addCharacterToLocation('), context);
    context.updateCharacterTeamVisual(1, 'Долг', '#00ff00');
    assert.equal(entry.teamBadge.style.borderLeftColor, '#00ff00');
    assert.equal(entry.teamBadge.style.backgroundColor, undefined);
    assert.equal(entry.teamBadge.style.display, 'block');
    assert.equal(entry.label.element.style['--character-affiliation'], '#00ff00');
    vm.runInContext(section('export function refreshUserColor(', 'export function applyCharacterPostureVisual('), context);
    context.refreshUserColor(7);
    assert.equal(entry.label.element.style.borderLeftColor, '#ff0000');
    assert.equal(entry.label.element.style.backgroundColor, 'rgba(17, 23, 20, 0.84)');
    assert.match(style, /\.location-character-label\s*\{[^}]*background: rgba\(17, 23, 20, 0\.84\)/);
    assert.match(scene, /div\.className = 'location-character-label'/);
});

test('character labels switch from full to name to marker with hysteresis and selection override', () => {
    const changes = [];
    const classes = new Set();
    const element = { classList: { toggle(name, enabled) {
        changes.push([name, enabled]);
        if (enabled) classes.add(name);
        else classes.delete(name);
    } } };
    const entry = { label: { position: { distance: 8 }, element } };
    const context = vm.createContext({scoutedCharacters:new Set(),
        camera: { position: { distanceTo(position) { return position.distance; } } },
        characterModels: new Map([[1, entry]]),
        window: { currentLocationCharacterId: null },
        hoveredCharacterId: null, dragCharacter: null, gmVisionCharacterId: null,
    });
    vm.runInContext(section('const CHARACTER_LABEL_NEAR_DISTANCE =', '// ========== Добавление персонажа'), context);
    const update = () => context.updateCharacterLabelDistances();
    update();
    assert.equal(entry.labelState, 'full');
    entry.label.position.distance = 27;
    update();
    assert.equal(entry.labelState, 'compact');
    assert.equal(classes.has('location-character-label--compact'), true);
    const before = changes.length;
    update();
    assert.equal(changes.length, before);
    entry.label.position.distance = 50;
    update();
    assert.equal(entry.labelState, 'marker');
    assert.equal(classes.has('location-character-label--marker'), true);
    context.window.currentLocationCharacterId = 1;
    update();
    assert.equal(entry.labelState, 'full');
    context.window.currentLocationCharacterId = null;
    update();
    assert.equal(entry.labelState, 'marker');
    entry.label.position.distance = 39;
    update();
    assert.equal(entry.labelState, 'marker');
    entry.label.position.distance = 37;
    update();
    assert.equal(entry.labelState, 'compact');
    entry.label.position.distance = 20;
    update();
    assert.equal(entry.labelState, 'compact');
    entry.label.position.distance = 15;
    update();
    assert.equal(entry.labelState, 'full');
    assert.match(style, /\.location-character-label--marker \.location-character-label__name\s*\{\s*display: none/);
    assert.match(style, /\.location-character-label--compact \.location-character-label__badge/);
});
