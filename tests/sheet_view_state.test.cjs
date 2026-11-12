const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('sheet refresh restores focus, caret, expanded panel and container', () => {
    const source = fs.readFileSync(path.join(__dirname, '../app/static/js/characterSheet.js'), 'utf8');
    const start = source.indexOf('function captureSheetViewState(');
    const end = source.indexOf('\nasync function refreshSavedSheet(', start);
    assert.ok(start >= 0 && end > start);
    const oldTab = {
        id: 'sheet-tab-health', scrollTop: 320, scrollLeft: 12, isConnected: false,
        getAttribute() { return null; }, classList: { contains() { return false; } },
        querySelectorAll() { return [oldDetail]; },
    };
    const newTab = { id: 'sheet-tab-health', scrollTop: 0, scrollLeft: 0, querySelectorAll() { return [newStressDetail, newDetail]; } };
    const oldDetail = { id: '', dataset: { sheetPanel: 'organs' }, open: true, closest() { return oldTab; } };
    const newStressDetail = { dataset: { sheetPanel: 'stress-effects' }, open: true };
    const newDetail = { dataset: { sheetPanel: 'organs' }, open: false };
    const oldControl = {
        id: 'health.current', selectionStart: 2, selectionEnd: 4,
        selectionDirection: 'forward', isConnected: false,
    };
    const newControl = {
        disabled: false,
        focus() { this.focused = true; },
        setSelectionRange(start, end, direction) { this.selection = [start, end, direction]; },
    };
    const oldContainer = {
        style: { display: 'block' },
        getAttribute() { return 'inventory,pockets,0,contents'; },
    };
    const toggle = { textContent: '▶' };
    const newContainer = {
        style: { display: 'none' }, parentElement: { _toggleIcon: toggle },
        getAttribute() { return 'inventory,pockets,0,contents'; },
    };
    let rerendered = false;
    const root = {
        contains(node) { return node === oldControl; },
        querySelectorAll(selector) {
            if (selector === '[id="health.current"]') return [rerendered ? newControl : oldControl];
            if (selector === 'details') return rerendered ? [newStressDetail, newDetail] : [oldDetail];
            if (selector === '[data-container-path]') return [rerendered ? newContainer : oldContainer];
            if (selector === '[id], .modal-content, [data-container-path]') return [oldTab];
            return [];
        },
    };
    const context = vm.createContext({
        CSS: { escape(value) { return value; } },
        document: {
            activeElement: oldControl,
            getElementById(id) {
                return id === 'character-sheet-modal' ? root : newTab;
            },
        },
    });
    vm.runInContext(source.slice(start, end), context);
    const state = context.captureSheetViewState();
    rerendered = true;
    context.restoreSheetViewState(state);
    assert.equal(newDetail.open, true);
    assert.equal(newContainer.style.display, 'block');
    assert.equal(toggle.textContent, '▼');
    assert.equal(newControl.focused, true);
    assert.deepEqual(newControl.selection, [2, 4, 'forward']);
    assert.equal(newTab.scrollTop, 320);
    assert.equal(newTab.scrollLeft, 12);

    rerendered = false;
    oldControl.isConnected = true;
    oldControl.setSelectionRange = (...selection) => { oldControl.restoredSelection = selection; };
    const sameNodeState = context.captureSheetViewState();
    oldControl.selectionStart = 0;
    oldControl.selectionEnd = 0;
    context.restoreSheetViewState(sameNodeState);
    assert.deepEqual(oldControl.restoredSelection, [2, 4, 'forward']);
});

test('item catalog requests repair tools and GM stress controls are not location-gated', () => {
    const source = fs.readFileSync(path.join(__dirname, '../app/static/js/characterSheet.js'), 'utf8');
    assert.match(source, /'headphones', 'glasses', 'gloves', 'jewelry', 'tool'/);
    assert.match(source, /window\.isGM \? '<button type="button"[^']*adjustCharacterStress\(-1\)/);
    assert.doesNotMatch(source, /window\.isGM && window\.currentLocationId \? '<button type="button"[^']*adjustCharacterStress/);
});

test('saved basic tab waits for its redraw before restoring permissions and focus', () => {
    const source = fs.readFileSync(path.join(__dirname, '../app/static/js/characterSheet.js'), 'utf8');
    const start = source.indexOf('async function refreshSavedSheet(');
    const end = source.indexOf('\nfunction applySheetEditPermissions(', start);
    const refresh = source.slice(start, end);
    assert.match(refresh, /activeTab === 'basic'\) await renderBasicTab\(currentCharacterData\)/);
    assert.ok(refresh.indexOf('await renderBasicTab') < refresh.indexOf('applySheetEditPermissions()'));
});

test('inventory container expansion survives a direct redraw and can be closed', () => {
    const source = fs.readFileSync(path.join(__dirname, '../app/static/js/characterSheet.js'), 'utf8');
    const start = source.indexOf('const expandedInventoryContainers = new Set();');
    const end = source.indexOf('\nfunction captureSheetViewState(', start);
    assert.ok(start >= 0 && end > start);
    const context = vm.createContext({ currentCharacterId: 12 });
    vm.runInContext(source.slice(start, end), context);
    const contents = { style: {}, dataset: {} };
    const toggle = {};
    const key = context.inventoryContainerKey({ id: 'bag-1' }, ['inventory', 0]);
    context.setInventoryContainerOpen(contents, toggle, key, true);
    assert.equal(contents.style.display, 'block');
    assert.equal(contents.dataset.containerStateKey, key);
    assert.equal(vm.runInContext('expandedInventoryContainers.has("12:id:bag-1")', context), true);
    const replacement = { style: {}, dataset: {} };
    context.setInventoryContainerOpen(replacement, toggle, key,
        vm.runInContext('expandedInventoryContainers.has("12:id:bag-1")', context));
    assert.equal(replacement.style.display, 'block');
    context.setInventoryContainerOpen(replacement, toggle, key, false);
    assert.equal(vm.runInContext('expandedInventoryContainers.has("12:id:bag-1")', context), false);
});

test('tool panel dragging binds to its header instead of its slider area', () => {
    const source = fs.readFileSync(path.join(__dirname, '../app/static/js/ui_interactions.js'), 'utf8');
    const start = source.indexOf('function makeDraggable(');
    const end = source.indexOf('\nfunction applyPosition(', start);
    assert.ok(start >= 0 && end > start);
    const listeners = { header: [], document: [] };
    const context = vm.createContext({
        document: { addEventListener(type) { listeners.document.push(type); } },
    });
    vm.runInContext(source.slice(start, end), context);
    const panel = { addEventListener() { assert.fail('panel body must not be a drag handle'); } };
    const header = { addEventListener(type) { listeners.header.push(type); } };
    context.makeDraggable(panel, header, 'panel-tools');
    assert.deepEqual(listeners.header, ['mousedown']);
    assert.deepEqual(listeners.document, ['mousemove', 'mouseup']);
});
