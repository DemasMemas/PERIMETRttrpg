const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const read = name => fs.readFileSync(path.join(__dirname, '../app/static/js', name), 'utf8');

test('the asset library offers storage crates but no new chests', async () => {
    const catalog = read('locationAssetCatalog.js');
    const { LOCATION_ASSETS } = await import(`data:text/javascript,${encodeURIComponent(catalog)}`);
    assert.equal(LOCATION_ASSETS.find(asset => asset.id === 'chest'), undefined);
    assert.ok(LOCATION_ASSETS.find(asset => asset.id === 'box'));
    assert.ok(LOCATION_ASSETS.find(asset => asset.id === 'military_crate'));

    const main = read('main.js');
    assert.doesNotMatch(main, /<option value="chest">/);
    assert.match(main, /<option value="military_crate">/);
    assert.match(main, /military_crate: \{ width: 1\.1, depth: 0\.8, height: 0\.65 \}/);
});

test('new and existing storage objects expose container actions even in old saves', async () => {
    const { findLocationAsset } = await import(`data:text/javascript,${encodeURIComponent(read('locationAssetCatalog.js'))}`);
    const source = read('locationScene.js');
    const start = source.indexOf('function getObjectActions(');
    const end = source.indexOf('\nfunction getStructureActions(', start);
    const interactionsStart = source.indexOf('const objectInteractions = {');
    const interactionsEnd = source.indexOf('\n};', interactionsStart);
    assert.ok(start >= 0 && end > start && interactionsStart >= 0 && interactionsEnd > interactionsStart);
    const context = vm.createContext({ isFireObject: () => false, findLocationAsset });
    vm.runInContext(source.slice(interactionsStart, interactionsEnd + 3), context);
    const stashStart = source.indexOf('const stashHosts =');
    const stashEnd = source.indexOf('function canReachLocationGenerator(', stashStart);
    assert.ok(stashStart >= 0 && stashEnd > stashStart);
    vm.runInContext(source.slice(stashStart, stashEnd) + source.slice(start, end), context);
    for (const type of ['box', 'military_crate', 'shelf', 'chest',
        ...['nightstand','wardrobe','dresser','wall_shelf','storage_rack','refrigerator','cooker',
            'kitchen_cabinet','sink','wood_stove','wood_crate','cardboard_box'].map(id => 'household_'+id)]) {
        assert.ok(context.getObjectActions({ type }).includes('open_container'));
        assert.ok(context.getObjectActions({ type, properties: { interactions: ['move', 'climb'] } })
            .includes('open_container'));
    }
    for (const type of ['table','chair','household_bed_single','household_generator','household_toilet']) {
        assert.ok(!context.getObjectActions({type}).includes('open_container'),type);
    }
    assert.match(source, /contents: \(findLocationAsset\(placedType\)\?\.container/);
});
