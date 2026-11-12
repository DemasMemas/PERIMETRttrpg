const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/characterSheet.js'), 'utf8');
const persistSource = source.slice(source.indexOf('function persistCurrentSheet('), source.indexOf('function scheduleAutoSave('));

test('обычное сохранение сверяет ответ сервера со снимком отправленных данных', async () => {
    const draft = { _revision: 1, appearance: { top: 'sweater' } };
    let reconciled;
    const context = vm.createContext({
        medicalOperationInProgress: false, currentCharacterCanEdit: true,
        currentCharacterId: 12, currentCharacterData: draft, autoSaveTimer: null,
        structuredClone, updateDataFromFields() {}, consumeManualFieldPaths: () => [],
        Server: {
            async updateCharacter(id, update) {
                assert.equal(id, 12);
                assert.equal(update.data, draft);
                draft._revision = 2;
                draft.appearance.top = 'gorka';
            },
            isCharacterSaving: () => false,
        },
        applyPendingSheetRemoteUpdate: async () => false,
        async applyRemoteSheetData(...args) { reconciled = args; },
        refreshSavedSheet() { assert.fail('Автосохранение не должно пересобирать весь лист'); },
        showNotification(message) { assert.fail(message); },
    });
    vm.runInContext(persistSource, context);
    await context.persistCurrentSheet();
    assert.equal(reconciled[0], draft);
    assert.equal(reconciled[1], 12);
    assert.deepEqual(reconciled[2], { _revision: 1, appearance: { top: 'sweater' } });
});

test('ожидающий новый ввод защищён от перерисовки предыдущим сохранением', async () => {
    let release;
    const context = vm.createContext({
        medicalOperationInProgress: false, currentCharacterCanEdit: true,
        currentCharacterId: 12, currentCharacterData: {}, autoSaveTimer: null,
        structuredClone, updateDataFromFields() {}, consumeManualFieldPaths: () => [],
        Server: {
            updateCharacter: () => new Promise(resolve => { release = resolve; }),
            isCharacterSaving: () => false,
        },
        applyPendingSheetRemoteUpdate: async () => false,
        applyRemoteSheetData() { assert.fail('Новый ввод ещё не сохранён'); },
        showNotification(message) { assert.fail(message); },
    });
    vm.runInContext(persistSource, context);
    const pending = context.persistCurrentSheet();
    context.autoSaveTimer = 1;
    release();
    await pending;
});

test('изменение внешности сразу попадает в локальные данные', () => {
    const editSource = source.slice(source.indexOf('function handleManualFormEdit('), source.indexOf('function renderCreatedByPlayerBadge('));
    const calls = [];
    const context = vm.createContext({
        currentCharacterCanEdit: true, sheetSaveBlocked: false,
        trackManualCharacterField() {},
        updateDataFromFields() { calls.push('update'); },
        scheduleAutoSave(delay) { calls.push(delay); },
    });
    vm.runInContext(editSource, context);
    context.handleManualFormEdit({ target: { getAttribute: () => 'appearance.top' }, type: 'change' });
    assert.deepEqual(calls, ['update', 200]);
});

test('обновление состояния вне боя не пересобирает экипировку', () => {
    const combatSource = source.slice(source.indexOf('let combatSheetRefreshBound = false;'), source.indexOf('if (!combatSheetRefreshBound)'));
    let renders = 0;
    const context = vm.createContext({
        currentCharacterData: {}, equipmentActivePanel: 'weapons',
        window: { locationCombatState: { status: 'inactive' } },
        document: {
            getElementById: () => ({ style: { display: 'flex' } }),
            querySelector: () => ({ dataset: { tab: 'equipment' } }),
        },
        renderEquipmentTab() { renders++; },
    });
    vm.runInContext(combatSource, context);
    context.refreshCombatSensitiveSheetTabs();
    assert.equal(renders, 0);
    context.window.locationCombatState.status = 'active';
    context.refreshCombatSensitiveSheetTabs();
    assert.equal(renders, 1);
    context.equipmentActivePanel = 'appearance';
    context.refreshCombatSensitiveSheetTabs();
    assert.equal(renders, 1);
    context.equipmentActivePanel = 'weapons';
    context.window.locationCombatState.status = 'inactive';
    context.refreshCombatSensitiveSheetTabs();
    assert.equal(renders, 2);
    context.refreshCombatSensitiveSheetTabs();
    assert.equal(renders, 2);
});
