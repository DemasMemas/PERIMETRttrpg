const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/characterSheet.js'), 'utf8');
const start = source.indexOf('function liquidCanisterKind(');
const end = source.indexOf('function calculateBackpackTotals(', start);
assert.ok(start >= 0 && end > start);
const context = vm.createContext({});
vm.runInContext(source.slice(start, end), context);

test('канистра принимает доли литра и не смешивает топливо с водой', () => {
    const canister = {name:'Канистра(Пустая)', attributes:{liquid_capacity_liters:15}, contents:[]};
    const fuel = {name:'Топливо(1 Литр)', quantity:.5};
    const water = {name:'Вода(1 Литр)', quantity:1};
    assert.equal(context.isLiquidCanister(canister), true);
    assert.equal(context.canStoreInLiquidCanister(canister, fuel), true);
    canister.contents.push(fuel);
    assert.equal(context.canStoreInLiquidCanister(canister, water), false);
    assert.equal(context.canStoreInLiquidCanister(canister, {name:'Металлические детали',quantity:1}), false);
    assert.equal(context.canStoreInLiquidCanister(canister, {name:'Топливо(1 Литр)',quantity:14.6}), false);
    canister.contents = [];
    canister.fuelUsed = true;
    assert.equal(context.canStoreInLiquidCanister(canister, water), false);
});
