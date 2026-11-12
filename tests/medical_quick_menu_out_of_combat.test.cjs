const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/locationScene.js'), 'utf8');

function functionSource(startText, endText) {
    const start = source.indexOf(startText);
    const end = source.indexOf(endText, start);
    assert.ok(start >= 0 && end > start);
    return source.slice(start, end).replace('export function ', 'function ');
}

function harness({ controlled = true, condition = 'active' } = {}) {
    const notices = [];
    const selected = [];
    const context = vm.createContext({
        combatState: null,
        findCombatCharacterByCharacterId: () => null,
        window: {},
        canControlCharacter: () => controlled,
        getLocationCharacterCondition: () => ({ state: condition }),
        getLocationCharacterById: id => ({ characterId: id, name: `Target ${id}` }),
        closeCombatMenus() {}, hideStructureInteraction() {},
        showNotification: message => notices.push(message),
    });
    vm.runInContext(`
        let pendingCombatAction = null;
        function clearPendingCombatAction() { pendingCombatAction = null; }
        ${functionSource('export function beginPendingCombatAction(', 'export function clearPendingCombatAction(')}
        ${functionSource('async function resolveCombatTargetSelection(', 'async function resolveCombatStructureSelection(')}
    `, context);
    const begin = () => context.beginPendingCombatAction({
        actorCharacterId: 1,
        actionKey: 'use_item',
        onResolve: async selection => { selected.push(selection); return true; },
    });
    return { context, begin, selected, notices };
}

test('quick medical target selection works outside combat, including the actor', async () => {
    const h = harness();
    assert.equal(h.begin(), true);
    assert.equal(await h.context.resolveCombatTargetSelection(1), true);
    assert.equal(h.selected.length, 1);
    assert.equal(h.selected[0].targetCharacterId, 1);
    assert.equal(vm.runInContext('pendingCombatAction', h.context), null);

    assert.equal(h.begin(), true);
    assert.equal(await h.context.resolveCombatTargetSelection(2), true);
    assert.equal(h.selected[1].targetCharacterId, 2);
});

test('quick medical target selection rejects uncontrolled or incapacitated actors', () => {
    assert.equal(harness({ controlled: false }).begin(), false);
    assert.equal(harness({ condition: 'dead' }).begin(), false);
});

test('starting combat cancels an unpaid out-of-combat medical selection', async () => {
    const h = harness();
    assert.equal(h.begin(), true);
    h.context.combatState = { status: 'active' };
    assert.equal(await h.context.resolveCombatTargetSelection(2), false);
    assert.equal(h.selected.length, 0);
    assert.equal(vm.runInContext('pendingCombatAction', h.context), null);
});

test('quick menu applies a blood collection kit to the doctor without requesting consent', async () => {
    const calls = [];
    const helper = functionSource(
        'async function applyQuickMedicalConsumable(',
        'export async function showMedicalConsumableMenu('
    ).replace("import('./characterSheet.js')", 'Promise.resolve(sheet)');
    const context = vm.createContext({
        sheet: { async useCharacterInventoryItem(...args) { calls.push(args); return true; } },
        canControlCharacter: () => true,
        getLocationCharacterCondition: () => ({ state: 'active' }),
        findCombatCharacterByCharacterId: () => ({ location_character_id: 10 }),
        Server: { async inspectLocationCharacter() { throw new Error('Self treatment must not inspect a patient'); } },
    });
    vm.runInContext(helper, context);
    const entry = { path: ['inventory', 'pockets', 0], item: { id: 'kit' } };
    assert.equal(await context.applyQuickMedicalConsumable(1, entry, 1), true);
    assert.equal(calls.length, 1);
    assert.equal(calls[0][2].targetCharacterId, 1);
    assert.equal(calls[0][2].requestTreatmentConsent, undefined);
});

test('quick menu requests consent when treating another conscious character', async () => {
    const calls = [];
    const helper = functionSource(
        'async function applyQuickMedicalConsumable(',
        'export async function showMedicalConsumableMenu('
    ).replace("import('./characterSheet.js')", 'Promise.resolve(sheet)');
    const context = vm.createContext({
        sheet: { async useCharacterInventoryItem(...args) { calls.push(args); return true; } },
        canControlCharacter: () => true,
        getLocationCharacterCondition: () => ({ state: 'active' }),
        findCombatCharacterByCharacterId: () => ({ location_character_id: 10 }),
        window: { currentLobbyId: 8 },
        getCurrentLocationId: () => 4,
        Server: { async inspectLocationCharacter() { return { target_data: { health: {} } }; } },
        requestTreatmentConsent: async () => ({ allowed: true, requestId: 7 }),
    });
    vm.runInContext(helper, context);
    assert.equal(await context.applyQuickMedicalConsumable(1, { path: [0], item: { id: 'kit' } }, 2), true);
    assert.equal(calls[0][2].targetCharacterId, 2);
    assert.equal((await calls[0][2].requestTreatmentConsent({})).requestId, 7);
    assert.equal(calls[0][2].interactionContext.actorLocationCharacterId, 10);
});

test('quick menu closes before opening the self-treatment injury picker', () => {
    assert.match(source, /querySelector\('\.medical-target-self'\)\.onclick = async \(\) => \{\s*try \{\s*closeMedicalConsumableMenu\(\);\s*await applyQuickMedicalConsumable\(/);
});
