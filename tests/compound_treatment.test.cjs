const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../app/static/js/characterSheet.js'), 'utf8');
const start = source.indexOf('export async function useCharacterInventoryItem(');
const end = source.indexOf('\nexport async function resolveMustDoMedicalRetry', start);

test('treatment notifications explain the 50 percent check and the actual bleeding outcome', () => {
    const begin = source.indexOf('function notifyMedicalProcedure(');
    const finish = source.indexOf('\nfunction notifyConsumableUse(', begin);
    const messages = [];
    const context = vm.createContext({
        showNotification: message => messages.push(message),
        BLEEDING_STAGE_LABEL: { light: 'лёгкого' },
    });
    vm.runInContext(source.slice(begin, finish), context);
    const common = { roll: 9, difficulty: 10, bleeding_grade: 'chance' };
    context.notifyMedicalProcedure('Бинт', {
        ...common, success: true, outcome: { mode: 'weaken', result_stage: 'light' },
    });
    context.notifyMedicalProcedure('Бинт', { ...common, success: false });
    context.notifyMedicalProcedure('Бинт', {
        ...common, roll: 15, bleeding_grade: 'improved', success: true, outcome: { mode: 'close' },
    });
    assert.match(messages[0], /50%.*ослаблено до лёгкого/);
    assert.match(messages[1], /50% не сработал.*Расходник потрачен/);
    assert.match(messages[2], /запасом 5.*остановлено/);
});
const clone = value => JSON.parse(JSON.stringify(value));
const notifyUse = source.slice(source.indexOf('function notifyConsumableUse('), source.indexOf('\nasync function useConsumable('));

test('a consumable and an acquired addiction appear in one notification', () => {
    const messages = [];
    const context = vm.createContext({ showNotification: message => messages.push(message) });
    vm.runInContext(notifyUse, context);
    context.notifyConsumableUse('Энергетик', { acquired: true, profile: { label: 'Кофеиновая' } });
    assert.deepEqual(messages, ['Энергетик использован. Получена зависимость: Кофеиновая.']);
});

function harness({ fail = false, deferred = false, self = false, failedRoll = false, noEffect = false, serverResult = false } = {}) {
    const actor = { _revision: 1, _character_id: 1, health: { current: 80 }, inventory: { backpack: [{ id: 'bandage', category: 'consumable', quantity: 2 }] } };
    const patient = { _revision: 1, _character_id: 2, health: { current: 80, effects: [] } };
    const modal = { inert: false };
    const sheetBackdrop = { inert: false };
    const messages = [];
    const sent = [];
    const context = vm.createContext({
        medicalOperationInProgress: false, medicalRemoteUpdate: null,
        autoSaveTimer: null, sheetSaveBlocked: false, currentCharacterId: 1,
        currentCharacterData: actor, allTemplatesCache: {},
        window: { currentLocationId: 1, locationCombatState: { status: 'active' } },
        document: {
            getElementById: () => sheetBackdrop,
            querySelector: selector => selector === '#character-sheet-modal > .modal-content' ? modal : null,
        }, clearTimeout() {},
        normalizeCharacterEffects() {}, renderInventoryTab() {}, refreshHealthPanel() {},
        async refreshSavedSheet() {}, showNotification: message => messages.push(message),
        Server: {
            async waitForCharacterSave() {},
            characterUpdatePayload: (id, data) => ({ data, _base_data: clone(id === 1 ? actor : patient) }),
            async treatLocationCharacter(lobby, location, target, payload) {
                assert.equal(modal.inert, true);
                assert.equal(sheetBackdrop.inert, false);
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
            } else {
                options.medicalOutcome = { message: 'Проверка Медицины провалена. Расходник потрачен.', type: 'system' };
            }
            options.treatmentRequestId = 7;
            return true;
        },
    });
    vm.runInContext(notifyUse + source.slice(start, end).replace('export ', ''), context);
    const run = () => context.useCharacterInventoryItem(1, ['inventory', 'backpack', 0], {
        targetCharacterId: self ? 1 : 2, targetData: patient,
        interactionContext: { lobbyId: 1, locationId: 1, actorLocationCharacterId: 10 },
    });
    return { context, actor, patient, modal, sent, messages, run };
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
    assert.deepEqual(h.messages, []);
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
    assert.deepEqual(h.messages, ['Проверка Медицины провалена. Расходник потрачен.']);
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

test('fully paid combat consumable is handed off to the same resumer as a long action', async () => {
    const combatState = { status: 'active', location_id: 5,
        current_character: { character_id: 1, location_character_id: 10 } };
    const item = { name: 'Вода', templateId: 21, category: 'consumable' };
    const pathInInventory = ['inventory', 'pockets', 0];
    let paymentCalls = 0;
    const context = vm.createContext({
        window: { locationCombatState: combatState, currentLobbyId: 4, currentLocationId: 5 },
        currentCharacterId: 1, currentCharacterData: { health: {} },
        async getAllItemTemplates() {
            return [{ id: 21, attributes: { consumable: { direct: { action_points_cost: 4 } } } }];
        },
        async calculateInventoryAccess() { return { retrievalActionPoints: 1, useActionDiscount: 0 }; },
        getInventoryItemAvailableUses: () => 1,
        showNotification: message => assert.fail(message),
        Server: {
            async spendLocationCombatResources(lobbyId, locationId, body) {
                paymentCalls++;
                assert.equal(lobbyId, 4);
                assert.equal(locationId, 5);
                assert.equal(body.action_points, 5);
                assert.equal(body.consumable_selection.server_operation, 'general');
                assert.equal(body.consumable_selection.target_character_id, 1);
                return { payment_complete: true, combat_state: combatState };
            },
            async applyGeneralConsumable() { assert.fail('Инициатор только оплачивает действие'); },
        },
    });
    const spendStart = source.indexOf('function spendInventoryAccessForCombat(');
    const spendEnd = source.indexOf('\nfunction combineCombatPayments(', spendStart);
    const useStart = source.indexOf('async function useConsumable(');
    const useEnd = source.indexOf('\nasync function useGrenade(', useStart);
    vm.runInContext(source.slice(spendStart, spendEnd) + source.slice(useStart, useEnd), context);
    const options = { preselectedApplication: { kind: 'self', actionPoints: 4 } };
    assert.equal(await context.useConsumable(item, pathInInventory, options), false);
    assert.equal(paymentCalls, 1);
    assert.equal(options.paidCombatState, combatState);
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
    assert.equal(await context.resumeDeferredConsumable(4, 5, 'paid'), null);
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

test('paid server medicine uses the saved patient, source, consent and toolkit choice with the sheet closed', async () => {
    const saved = { status: 'ready', id: 'paid', actor_character_id: 1, actor_location_character_id: 10,
        selection: {
            source: { path: ['inventory', 'pockets', 0], snapshot: { name: 'Стерильный Бинт' } },
            target_character_id: 2, application: { kind: 'bleeding', effect: { id: 'bleed-1' } },
            treatment_request_id: 9, server_operation: 'medical', skip_toolkits: true,
            confirmed_preview: { difficulty: 8 },
        } };
    const context = vm.createContext({
        currentCharacterId: null,
        medicalOperationInProgress: false,
        Server: {
            async prepareDeferredConsumable() { return saved; },
            async applyMedicalProcedure(actorId, body) {
                assert.equal(actorId, 1);
                assert.equal(body.target_character_id, 2);
                assert.equal(body.deferred_action_id, 'paid');
                assert.equal(body.interaction_request_id, 9);
                assert.equal(body.actor_location_character_id, 10);
                assert.equal(body.combat_location_id, 5);
                assert.equal(body.source, saved.selection.source);
                assert.equal(body.application, saved.selection.application);
                assert.equal(body.skip_toolkits, true);
                assert.equal(body.confirmed_preview.difficulty, 8);
                return { medical_result: { success: true, roll: 16 } };
            },
        },
        async confirmMedicalRoll(actorId, body, preview) {
            assert.equal(actorId, 1);
            body.confirmed_preview = preview;
            return true;
        },
        notifyMedicalProcedure(name, result) {
            assert.equal(name, 'Стерильный Бинт');
            assert.equal(result.roll, 16);
        },
        receiveSheetRemoteData(_id, data) { assert.equal(data, undefined); },
        useCharacterInventoryItem() { throw new Error('A paid server procedure must not reopen the inventory'); },
    });
    const begin = source.indexOf('export async function resumeDeferredConsumable(');
    vm.runInContext(source.slice(begin, start).replace('export ', ''), context);
    assert.equal(await context.resumeDeferredConsumable(4, 5, 'paid'), null);
    assert.equal(context.currentCharacterId, null);
});

test('a combat update received after completion does not apply an ordinary consumable again', async () => {
    const context = vm.createContext({
        medicalOperationInProgress: false,
        Server: {
            async prepareDeferredConsumable() { return { status: 'completed' }; },
            async applyGeneralConsumable() { throw new Error('Already applied'); },
        },
    });
    const begin = source.indexOf('export async function resumeDeferredConsumable(');
    vm.runInContext(source.slice(begin, start).replace('export ', ''), context);
    assert.equal(await context.resumeDeferredConsumable(4, 5, 'paid'), null);
});

test('deferred result carries the persisted receipt ID into the atomic save', async () => {
    const h = harness({ self: true });
    await h.context.useCharacterInventoryItem(1, ['inventory', 'backpack', 0], { deferredActionId: 'saved-medical' });
    assert.equal(h.sent[0].consumable_use.id, 'saved-medical');
    assert.equal(h.sent[0].consumable_use.deferred_action_id, 'saved-medical');
});
