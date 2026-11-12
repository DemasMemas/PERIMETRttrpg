const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../app/static/js/characterSheet.js'), 'utf8');
const start = source.indexOf('export async function useCharacterInventoryItem(');
const end = source.indexOf('\nexport async function resolveMustDoMedicalRetry', start);
const clone = value => JSON.parse(JSON.stringify(value));

function harness({ fail = false, deferred = false, self = false, failedRoll = false, noEffect = false, serverResult = false } = {}) {
    const actor = { _revision: 1, _character_id: 1, health: { current: 80 }, inventory: { backpack: [{ id: 'bandage', category: 'consumable', quantity: 2 }] } };
    const patient = { _revision: 1, _character_id: 2, health: { current: 80, effects: [] } };
    const modal = { inert: false };
    const sent = [];
    const context = vm.createContext({
        medicalOperationInProgress: false, medicalRemoteUpdate: null,
        autoSaveTimer: null, sheetSaveBlocked: false, currentCharacterId: 1,
        currentCharacterData: actor, allTemplatesCache: {},
        window: { currentLocationId: 1, locationCombatState: { status: 'active' } },
        document: { getElementById: () => modal }, clearTimeout() {},
        normalizeCharacterEffects() {}, renderInventoryTab() {}, refreshHealthPanel() {},
        async refreshSavedSheet() {}, showNotification() {},
        Server: {
            async waitForCharacterSave() {},
            characterUpdatePayload: (id, data) => ({ data, _base_data: clone(id === 1 ? actor : patient) }),
            async treatLocationCharacter(lobby, location, target, payload) {
                assert.equal(modal.inert, true);
                sent.push(clone(payload));
                if (fail) throw new Error('conflict');
                return { actor_data: { ...clone(payload.actor_updates.data), _revision: 2 } };
            },
            async finishConsumableUse(id, payload) {
                sent.push(clone(payload));
                if (fail) throw new Error('conflict');
                return { data: { ...clone(payload.actor_updates.data), _revision: 2 } };
            },
            async registerCharacterAddictionExposure() { throw new Error('Separate exposure is forbidden'); },
            async adjustLocationCombatResources() { throw new Error('Separate AP effect is forbidden'); },
            async updateCharacter() { throw new Error('Separate actor write is forbidden'); },
        },
        getItemByPath: () => context.currentCharacterData.inventory.backpack[0],
        async useItem(item, itemPath, options) {
            if (deferred) return false;
            if (noEffect) return undefined;
            if (serverResult) {
                options.serverConsumableResult = {
                    data: { ...clone(actor), _revision: 2, health: { current: 95 } },
                };
                return true;
            }
            item.quantity -= 1;
            if (!failedRoll) {
                options.targetData.health.current += 10;
                options.consumableSideEffects.exposure = { item_name: 'Банка пива', price: 100 };
                options.consumableSideEffects.action_points_delta = 2;
            }
            options.treatmentRequestId = 7;
            return true;
        },
    });
    vm.runInContext(source.slice(start, end).replace('export ', ''), context);
    const run = () => context.useCharacterInventoryItem(1, ['inventory', 'backpack', 0], {
        targetCharacterId: self ? 1 : 2, targetData: patient,
        interactionContext: { lobbyId: 1, locationId: 1, actorLocationCharacterId: 10 },
    });
    return { context, actor, patient, modal, sent, run };
}

test('treatment submits expense and target in one request and keeps original target untouched', async () => {
    const h = harness();
    assert.equal(await h.run(), true);
    assert.equal(h.sent.length, 1);
    assert.equal(h.sent[0].actor_updates.data.inventory.backpack[0].quantity, 1);
    assert.equal(h.sent[0].health.current, 90);
    assert.equal(h.sent[0].target_revision, 1);
    assert.equal(h.sent[0].interaction_request_id, 7);
    assert.equal(h.sent[0].consumable_use.side_effects.action_points_delta, 2);
    assert.equal(h.sent[0].consumable_use.side_effects.exposure.item_name, 'Банка пива');
    assert.equal(h.sent[0].consumable_use.source.snapshot.quantity, 2);
    assert.ok(h.sent[0].consumable_use.id);
    assert.equal(h.patient.health.current, 80);
    assert.equal(h.actor.inventory.backpack[0].quantity, 1);
    assert.equal(h.actor._revision, 2);
    assert.equal(h.context.currentCharacterData, h.actor);
    assert.equal(h.modal.inert, false);
});

test('failed compound request restores the inventory draft and unlocks the sheet', async () => {
    const h = harness({ fail: true });
    await assert.rejects(h.run(), /conflict/);
    assert.equal(h.actor.inventory.backpack[0].quantity, 2);
    assert.equal(h.patient.health.current, 80);
    assert.equal(h.context.currentCharacterData, h.actor);
    assert.equal(h.context.medicalOperationInProgress, false);
    assert.equal(h.modal.inert, false);
});

test('deferred action does not consume or heal before completion', async () => {
    const h = harness({ deferred: true });
    assert.equal(await h.run(), false);
    assert.equal(h.sent.length, 0);
    assert.equal(h.actor.inventory.backpack[0].quantity, 2);
    assert.equal(h.patient.health.current, 80);
    assert.equal(h.modal.inert, false);
});

test('second concurrent application is rejected after waiting for prior saves', async () => {
    const h = harness();
    const results = await Promise.allSettled([h.run(), h.run()]);
    assert.equal(results.filter(result => result.status === 'fulfilled').length, 1);
    assert.equal(h.sent.length, 1);
});

test('self use includes its side effects in the single commit request', async () => {
    const h = harness({ self: true });
    await h.run();
    assert.equal(h.sent.length, 1);
    assert.equal(h.sent[0].consumable_use.combat_location_id, 1);
    assert.equal(h.sent[0].consumable_use.side_effects.action_points_delta, 2);
    assert.equal(h.actor.health.current, 90);
    assert.equal(h.actor.inventory.backpack[0].quantity, 1);
    assert.equal(h.actor._revision, 2);
});

test('server-authoritative ordinary consumable skips the legacy sheet save', async () => {
    const h = harness({ self: true, serverResult: true });
    assert.equal(await h.run(), true);
    assert.equal(h.sent.length, 0);
    assert.equal(h.actor.health.current, 95);
    assert.equal(h.actor._revision, 2);
    assert.equal(h.modal.inert, false);
});

test('failed self commit does not leave spent consumables or healed health locally', async () => {
    const h = harness({ self: true, fail: true });
    await assert.rejects(h.run(), /conflict/);
    assert.equal(h.actor.health.current, 80);
    assert.equal(h.actor.inventory.backpack[0].quantity, 2);
    assert.equal(h.modal.inert, false);
});

test('failed medicine roll consumes a charge but sends no successful side effects', async () => {
    const h = harness({ self: true, failedRoll: true });
    await h.run();
    assert.deepEqual(h.sent[0].consumable_use.side_effects, {});
    assert.equal(h.actor.health.current, 80);
    assert.equal(h.actor.inventory.backpack[0].quantity, 1);
});

test('a consumable without an applicable effect is not recorded as successfully applied', async () => {
    const h = harness({ self: true, noEffect: true });
    assert.equal(await h.run(), false);
    assert.equal(h.sent.length, 0);
    assert.equal(h.actor.inventory.backpack[0].quantity, 2);
    assert.equal(h.modal.inert, false);
});

test('inventory consumable entry uses the transaction wrapper, not the legacy mutator', async () => {
    const begin = source.indexOf('function isInventoryPda(');
    const end = source.indexOf('\nfunction getRepairTargetCategory', begin);
    const calls = [];
    const context = vm.createContext({
        currentCharacterId: 12,
        async useCharacterInventoryItem(...args) { calls.push(args); return 'compound'; },
        async useConsumable() { throw new Error('No direct consumable mutation'); },
    });
    vm.runInContext(source.slice(begin, end), context);
    const result = await context.useItem({ category: 'consumable' }, ['inventory', 'backpack', 0]);
    assert.equal(result, 'compound');
    assert.equal(calls[0][0], 12);
    const consumable = source.slice(source.indexOf('async function useConsumable('), source.indexOf('async function useGrenade('));
    assert.ok(!consumable.includes('Server.registerCharacterAddictionExposure'));
    assert.ok(!consumable.includes('Server.adjustLocationCombatResources'));
});

test('saved medical completion loads fresh patient and uses the original operation ID without an open sheet', async () => {
    const calls = [];
    const saved = { id: 'paid', actor_character_id: 1, actor_location_character_id: 10, medicine_roll: 17,
        target_data: { _revision: 7, health: { effects: [] } },
        selection: { source: { path: ['inventory', 'backpack', 0] }, target_character_id: 2,
            application: { kind: 'bleeding' }, treatment_request_id: 9 } };
    const context = vm.createContext({
        medicalOperationInProgress: true,
        Server: { async prepareDeferredConsumable(...args) { calls.push(args); return saved; } },
        async useCharacterInventoryItem(...args) { calls.push(args); return true; },
    });
    const begin = source.indexOf('export async function resumeDeferredConsumable(');
    vm.runInContext(source.slice(begin, start).replace('export ', ''), context);
    assert.equal(await context.resumeDeferredConsumable(4, 5, 'paid'), false);
    assert.equal(calls.length, 0);
    context.medicalOperationInProgress = false;
    assert.equal(await context.resumeDeferredConsumable(4, 5, 'paid'), true);
    assert.deepEqual(calls[0], [4, 5, 'paid']);
    assert.equal(calls[1][0], 1);
    assert.equal(calls[1][2].skipCombatPayment, true);
    assert.equal(calls[1][2].forceReload, true);
    assert.equal(calls[1][2].deferredActionId, 'paid');
    assert.equal(calls[1][2].medicineRoll, 17);
    assert.equal(calls[1][2].treatmentRequestId, 9);
    assert.equal(calls[1][2].targetData._revision, 7);
});

test('unsuccessful saved application is not announced as completed', async () => {
    const context = vm.createContext({
        medicalOperationInProgress: false,
        Server: { async prepareDeferredConsumable() { return { selection: { source: { path: [] } } }; } },
        async useCharacterInventoryItem() { return false; },
    });
    const begin = source.indexOf('export async function resumeDeferredConsumable(');
    vm.runInContext(source.slice(begin, start).replace('export ', ''), context);
    await assert.rejects(context.resumeDeferredConsumable(4, 5, 'paid'), /Процедура не завершена/);
});

test('deferred result carries the persisted receipt ID into the atomic save', async () => {
    const h = harness({ self: true });
    await h.context.useCharacterInventoryItem(1, ['inventory', 'backpack', 0], { deferredActionId: 'saved-medical' });
    assert.equal(h.sent[0].consumable_use.id, 'saved-medical');
    assert.equal(h.sent[0].consumable_use.deferred_action_id, 'saved-medical');
});
