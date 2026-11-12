const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/locationScene.js'), 'utf8');

test('interaction menu remains open until an outside click', () => {
    const start = source.indexOf('function ensureStructureActionMenu() {');
    const end = source.indexOf('\nfunction ensureStructureRotationMenu()', start);
    assert.ok(start >= 0 && end > start);
    const menu = { style: { display: 'block' }, addEventListener() {}, contains: target => target === menu };
    let click;
    let closes = 0;
    const context = vm.createContext({
        document: {
            createElement: () => menu,
            body: { appendChild() {} },
            addEventListener: (name, callback) => { if (name === 'click') click = callback; },
        },
        handlers: { document: {} },
    });
    vm.runInContext(`let structureActionMenu = null;
        function hideStructureInteraction() { closes++; structureActionMenu.style.display = 'none'; }
        ${source.slice(start, end)}`, context);
    context.closes = closes;
    vm.runInContext('ensureStructureActionMenu()', context);

    click({ target: menu });
    assert.equal(context.closes, 0);
    click({ target: {} });
    assert.equal(context.closes, 1);
    click({ target: {} });
    assert.equal(context.closes, 1);
});

test('leaving a model does not close the interaction menu, and selecting an action ends it first', () => {
    assert.match(source, /if \(pendingStructureAction && !pendingObjectMoveId\) \{\s*const menuOpen = structureActionMenu\?\.style\.display === 'block';/);
    assert.match(source, /if \(!menuOpen\) clearStructureActionMenu\(\)/);
    assert.match(source, /if \(structureActionMenu\?\.style\.display === 'block'\) \{\s*e\.preventDefault\(\);\s*e\.stopImmediatePropagation\(\);\s*hideStructureInteraction\(\)/);
    assert.match(source, /button\.onclick = async event => \{\s*event\.stopPropagation\(\);\s*hideStructureInteraction\(\);\s*try \{\s*await action\.run\(\)/);
    assert.match(source, /const actorCharacterId = pendingStructureAction\?\.actorCharacterId \|\| null;\s*hideStructureInteraction\(\);\s*await executeStructureAction\(object, item\.actionKey, actorCharacterId\)/);
});

test('hovering another valid target swaps the open interaction menu', () => {
    const start = source.indexOf('    const onMouseMove = (e) => {',
        source.indexOf('function setupCharacterDragging()'));
    const end = source.indexOf('    const onMouseMoveProfiled = (event) => {', start);
    assert.ok(start >= 0 && end > start);
    const opened = [];
    const context = vm.createContext({
        isDraggingCharacter: true,
        armedMoveCharacterId: null,
        pendingFacingSelection: null,
        pendingCharacterPlacement:null,
        pendingStationPlacement:null,
        pendingCombatAction: null,
        pendingObjectMoveId: null,
        pendingStructureAction: { actorCharacterId: 1 },
        structureActionMenu: { style: { display: 'none' } },
        structureActionMenuState: null,
        hoveredStructureObjectId: null,
        structureHoverLastObjectId: null,
        canvas: { style: {} },
        getCharacterAtScreen: x => [10, 20].includes(x)
            ? { userData: { characterId: x === 10 ? 2 : 3 } } : null,
        canInteractWithCharacter: () => true,
        clearTerrainClimbTarget() {}, showTerrainClimbMenu() {},
        showCharacterInteractionMenu: (_x, _y, id) => {
            opened.push(`character:${id}`);
            context.structureActionMenuState = { objectId: `character:${id}` };
            context.structureActionMenu.style.display = 'block';
        },
        getLocationObjectAtScreen: x => x === 30
            ? { userData: { locationObject: { id: 4 } } } : null,
        getStructureActions: () => ['open'],
        isStructureActionAllowed: () => true,
        showStructureActionMenu: () => {
            opened.push('structure:4');
            context.structureActionMenuState = { objectId: 4 };
        },
        clearStructureActionMenu: () => { throw new Error('Menu closed during hover'); },
    });
    vm.runInContext(source.slice(start, end), context);
    for (const x of [10, 0, 20, 30, 30]) {
        vm.runInContext(`onMouseMove({ clientX: ${x}, clientY: 0 })`, context);
    }
    assert.deepEqual(opened, ['character:2', 'character:3', 'structure:4']);
});

test('butchering close button matches the mutant card and menus anchor to model centers', () => {
    assert.match(source, /class="close mutant-card-close" aria-label="Закрыть"/);
    assert.match(source, /const model = getCharacterModelEntry\(targetCharacterId\)\?\.model;[\s\S]*?model\.getWorldPosition\(anchor\);[\s\S]*?anchor\.y \+= 0\.9;/);
    assert.match(source, /structureActionMenu\.style\.display = 'block';\s*const rect = structureActionMenu\.getBoundingClientRect\(\)/);
});
