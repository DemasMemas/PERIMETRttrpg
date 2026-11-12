const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const moduleUrl = 'data:text/javascript;base64,' + Buffer.from(fs.readFileSync(
    path.join(__dirname, '../app/static/js/characterSheetSync.js'), 'utf8',
)).toString('base64');
const load = () => import(moduleUrl);

test('серверный профиль артефактов не считается редактированием листа',async()=>{
    const {diffCharacterData}=await load();
    assert.deepEqual(diffCharacterData({_artifact_profile:{movement_penalty:0}},{_artifact_profile:{movement_penalty:2}}),
        {scalarPaths:[],structuralPaths:[]});
});

test('character sheet diff separates scalar fields from structural lists', async () => {
    const { diffCharacterData } = await load();
    const result = diffCharacterData(
        {
            _revision: 1,
            notes: 'old',
            health: { current: 100, effects: [{ type: 'pain', value: 1 }] },
            inventory: { backpack: [{ id: 'a' }] },
        },
        {
            _revision: 2,
            notes: 'new',
            health: { current: 80, effects: [{ type: 'pain', value: 2 }] },
            inventory: { backpack: [{ id: 'a' }, { id: 'b' }] },
        },
    );

    assert.deepEqual(result.scalarPaths.sort(), ['health.current', 'notes']);
    assert.deepEqual(result.structuralPaths.sort(), [
        'health.effects', 'inventory.backpack',
    ]);
});

test('character sheet diff reports added scalar and object fields without revision noise', async () => {
    const { diffCharacterData, pathTouches } = await load();
    const result = diffCharacterData(
        { _revision: 3, basic: { age: 20 } },
        { _revision: 4, basic: { age: 21, faction: 'Долг' }, equipment: { helmet: null } },
    );

    assert.deepEqual(result.scalarPaths.sort(), ['basic.age', 'basic.faction']);
    assert.deepEqual(result.structuralPaths, ['equipment']);
    assert.equal(pathTouches('health.zones.head.current', 'health'), true);
    assert.equal(pathTouches('skills.physical.will', 'health'), false);
});

test('character sheet diff ignores unchanged arrays from a fresh server snapshot', async () => {
    const { diffCharacterData } = await load();
    const inventory = { backpack: [{ id: 'a', quantity: 2, contents: [{ id: 'b', quantity: 1 }] }], pockets: [] };
    const savedInventory = { backpack: [{ contents: [{ quantity: 1, id: 'b' }], quantity: 2, id: 'a' }], pockets: [] };
    const result = diffCharacterData(
        { _revision: 1, notes: 'old', inventory },
        { _revision: 2, notes: 'new', inventory: savedInventory },
    );

    assert.deepEqual(result.scalarPaths, ['notes']);
    assert.deepEqual(result.structuralPaths, []);
});

test('awareness is one listed skill with a separate check mode', () => {
    const locationSource = fs.readFileSync(
        path.join(__dirname, '../app/static/js/locationScene.js'), 'utf8',
    );
    const catalog = locationSource.slice(
        locationSource.indexOf('const NARRATIVE_SKILLS'),
        locationSource.indexOf('const AWARENESS_SKILL_PATH'),
    );
    const sheetSource = fs.readFileSync(
        path.join(__dirname, '../app/static/js/characterSheet.js'), 'utf8',
    );
    const sheetCatalog = sheetSource.slice(
        sheetSource.indexOf('const skillCategories'),
        sheetSource.indexOf('const skillXpRequirements'),
    );

    assert.match(catalog, /\['skills\.physical\.awareness', 'Внимательность'\]/);
    assert.doesNotMatch(catalog, /awareness\.(visual|hearing)/);
    assert.match(sheetCatalog, /\{ label: 'Внимательность', path: 'physical\.awareness' \}/);
    assert.doesNotMatch(sheetCatalog, /awareness\.(visual|hearing)/);
    assert.match(locationSource, /function awarenessModePath\(form\)/);
    assert.match(locationSource, /name="awareness_mode"/);
    assert.match(sheetSource, /getAwarenessModeChoices\(currentCharacterData, darkness\)/);
});
