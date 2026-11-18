const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../app/static/js/characterWidgets.js'), 'utf8');
const context = vm.createContext({});
const importLine = /^import [^\r\n]*\r?\n/gm;
vm.runInContext(source.replace(importLine, '').replaceAll('export ', ''), context);

test('widget action references accept existing actions and literal arguments only', () => {
    assert.deepEqual(JSON.parse(JSON.stringify(context.parseWidgetAction("window.useMeleeAttack(2, 'Рубящий', true)"))),
        { name: 'useMeleeAttack', args: [2, 'Рубящий', true] });
    for (const text of ['alert(1)', 'removeWeapon(0)', 'rollSkill(window.token)', "rollSkill('a');alert(1)", 'rollSkill(1,)']) {
        assert.equal(context.parseWidgetAction(text), null);
    }
});

test('weapon pins follow item identity after reorder and reject removed or ambiguous items', () => {
    const weapon = { id: 'w1', name: 'Gun' };
    const binding = context.itemBinding({ weapons: [weapon] }, 'weapons.0.ammo');
    const reference = { kind: 'field', path: 'weapons.0.ammo', binding };
    assert.equal(context.resolveWidgetReference(reference, { weapons: [{ id: 'w2' }, weapon] }).path, 'weapons.1.ammo');
    assert.equal(context.resolveWidgetReference(reference, { weapons: [{ id: 'w2' }] }), null);
    assert.equal(context.resolveWidgetReference(reference, { weapons: [weapon, weapon] }), null);
    const action = { kind: 'action', action: { name: 'reloadFixedMagazine', args: [0] }, binding };
    assert.equal(context.resolveWidgetReference(action, { weapons: [{ id: 'w2' }, weapon] }).action.args[0], 1);
});

test('armor pins stop targeting a slot when another item replaces it', () => {
    const data = { equipment: { armor: { templateId: 3, name: 'Old' } } };
    const reference = { path: 'equipment.armor.durability', binding: context.itemBinding(data, 'equipment.armor.durability') };
    assert.ok(context.resolveWidgetReference(reference, data));
    assert.equal(context.resolveWidgetReference(reference, { equipment: { armor: { templateId: 4, name: 'New' } } }), null);
});

test('inventory blocks follow nested items between containers and reject duplicate identities', () => {
    const item = { id: 'medicine-1', name: 'Медикамент', quantity: 3 };
    const original = { inventory: { pockets: [item], backpack: [] } };
    const ref = { kind: 'block', block: 'inventory-item', path: 'inventory.pockets.0', binding: context.itemBinding(original, 'inventory.pockets.0') };
    const moved = { inventory: { pockets: [], backpack: [{ id: 'bag', contents: [item] }] } };
    assert.equal(context.resolveWidgetReference(ref, moved).path, 'inventory.backpack.0.contents.0');
    moved.inventory.pockets.push(item);
    assert.equal(context.resolveWidgetReference(ref, moved), null);
    assert.equal(context.widgetReferenceIdentity(ref), context.widgetReferenceIdentity({ ...ref, path: 'inventory.backpack.0', binding: { ...ref.binding, root: 'inventory.backpack.0' } }));
});

test('effect blocks follow the effect ID after removal of a preceding effect', () => {
    const effect = { id: 'effect-2', type: 'pain', value: 2 };
    const original = { health: { effects: [{ id: 'effect-1' }, effect] } };
    const ref = { kind: 'block', block: 'effect', path: 'health.effects.1', binding: context.itemBinding(original, 'health.effects.1') };
    assert.equal(context.resolveWidgetReference(ref, { health: { effects: [effect] } }).path, 'health.effects.0');
    assert.equal(context.resolveWidgetReference(ref, { health: { effects: [] } }), null);
});

test('block controls normalize weapon positions and derived prices but retain attack identity', () => {
    const blockSource = fs.readFileSync(require('node:path').join(__dirname, '../app/static/js/characterWidgetBlocks.js'), 'utf8');
    vm.runInContext(blockSource.replace(importLine, '').replaceAll('export ', ''), context);
    const control = text => ({ name: '', dataset: {}, id: '', getAttribute: () => text });
    const key = (index, text) => context.blockControlKey({ reference: { path: `weapons.${index}` } }, control(text));
    assert.equal(key(0, "useWeaponFromEquipment(0, 'unaimed', 1, 4)"), key(3, "useWeaponFromEquipment(3, 'unaimed', 1, 2)"));
    assert.notEqual(key(0, "useWeaponFromEquipment(0, 'unaimed', 1, 4)"), key(0, "useWeaponFromEquipment(0, 'aimed', 1, 4)"));
});

test('pinned attacks resolve fresh sheet prices instead of replaying stored action points', () => {
    const button = { getAttribute: () => "useWeaponFromEquipment(0, 'unaimed', 1, 4)" };
    const reference = { kind: 'action', action: { name: 'useWeaponFromEquipment', args: [0, 'unaimed', 1, 999] } };
    const source = context.getWidgetSource({ querySelectorAll: () => [button] }, reference);
    assert.equal(source, button);
    assert.equal(context.parseWidgetAction(source.getAttribute('onclick')).args[3], 4);
});

test('preferences are scoped by caller key and damaged records do not create widgets', () => {
    const pin = { id: 'pin-1', characterId: 7, title: 'Health', references: [{ kind: 'field', path: 'health.current', title: 'Health' }] };
    const storage = { getItem: key => key === 'user1' ? JSON.stringify([pin, null, { ...pin, characterId: -1 }]) : null };
    assert.equal(context.readWidgetPreferences(storage, 'user1').length, 1);
    assert.equal(context.readWidgetPreferences(storage, 'user2').length, 0);
    assert.equal(context.readWidgetPreferences({ getItem: () => '{' }, 'bad').length, 0);
    assert.equal(context.widgetValue({}, '__proto__.polluted'), undefined);
    const removed = ['loadout', 'inventory-actions'].map(block => ({ ...pin, references: [{ kind: 'block', block, title: block }] }));
    assert.equal(context.readWidgetPreferences({ getItem: () => JSON.stringify(removed) }, 'old').length, 0);
});

test('old item, container and magazine pins use one block and retain the first panel identity', () => {
    const references = ['inventory-item', 'inventory-container', 'inventory-magazine'].map(block => ({
        kind: 'block', block, title: '9х39', path: 'inventory.pockets.0',
        binding: { root: 'inventory.pockets.0', identity: { id: 'ammo-1' } },
    }));
    const pins = references.map((ref, index) => ({ id: `pin-${index}`, characterId: 7, title: '9х39', references: [ref] }));
    const restored = context.readWidgetPreferences({ getItem: () => JSON.stringify(pins) }, 'old');
    assert.equal(restored.length, 1);
    assert.equal(restored[0].id, 'pin-0');
    assert.equal(restored[0].references[0].block, 'inventory-item');
    const legacyOnly = context.readWidgetPreferences({ getItem: () => JSON.stringify([pins[1]]) }, 'old');
    assert.equal(legacyOnly[0].id, 'pin-1');
    assert.equal(legacyOnly[0].references[0].block, 'inventory-item');
    assert.equal(context.resolveWidgetReference(references[1], { inventory: { pockets: [{ id: 'ammo-1' }] } }).block, 'inventory-item');
});

test('legacy weapon mastery panel becomes one personal panel per weapon type', () => {
    const pin = { id: 'mastery-old', characterId: 7, title: 'Владение оружием', references: [{ kind: 'block', block: 'weapon-mastery', title: 'Владение оружием' }] };
    const restored = context.readWidgetPreferences({ getItem: () => JSON.stringify([pin]) }, 'old');
    assert.equal(restored.length, 7);
    assert.equal(restored[0].id, pin.id);
    assert.equal(new Set(restored.map(entry => entry.id)).size, 7);
    assert.equal(new Set(restored.map(entry => entry.references[0].block)).size, 7);
    assert.equal(restored[0].references[0].block, 'weapon-mastery:pistols');
    assert.equal(restored[6].references[0].block, 'weapon-mastery:machineGuns');
});

test('disposing a removed widget releases its document drag listeners', () => {
    const dragSource = fs.readFileSync(require('node:path').join(__dirname, '../app/static/js/ui_interactions.js'), 'utf8');
    const start = dragSource.indexOf('function makeDraggable('), end = dragSource.indexOf('\nfunction applyPosition(', start);
    const documentListeners = new Map(), headerListeners = new Map();
    const events = listeners => ({ addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: (name, fn) => {
        assert.equal(listeners.get(name), fn); listeners.delete(name);
    } });
    const drag = vm.createContext({ document: events(documentListeners) });
    vm.runInContext(dragSource.slice(start, end), drag);
    const dispose = drag.makeDraggable({}, events(headerListeners), 'widget');
    assert.equal(documentListeners.size, 2); assert.equal(headerListeners.size, 1);
    dispose();
    assert.equal(documentListeners.size, 0); assert.equal(headerListeners.size, 0);
});
