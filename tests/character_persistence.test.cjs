const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const moduleUrl = 'data:text/javascript;base64,' + Buffer.from(fs.readFileSync(
    path.join(__dirname, '../app/static/js/characterPersistence.js'), 'utf8',
)).toString('base64');
const load = () => import(moduleUrl);
const clone = value => JSON.parse(JSON.stringify(value));

test('сохранение набранного текста принимает новый профиль артефактов с сервера',async()=>{
    const {mergeLocalChanges}=await load();
    const result=mergeLocalChanges({notes:'',_artifact_profile:{movement_penalty:0}},
        {notes:'текст',_artifact_profile:{movement_penalty:999}},
        {notes:'',_artifact_profile:{movement_penalty:2}});
    assert.equal(result.notes,'текст');assert.equal(result._artifact_profile.movement_penalty,2);
});

test('queued saves keep typing and server normalization without a stale token', async () => {
    const { createCharacterSaver } = await load();
    const sent = [];
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    const saver = createCharacterSaver(async (id, update) => {
        sent.push(clone(update));
        if (sent.length === 1) await gate;
        return { data: { ...update.data, normalized: true, _revision: sent.length + 1 } };
    });
    const data = { notes: 'a', _revision: 1 };
    const first = saver.save(1, { data });
    data.notes = 'ab';
    const second = saver.save(1, { data });
    assert.equal(saver.isSaving(1), true);
    release();
    await Promise.all([first, second]);
    assert.equal(sent.length, 2);
    assert.equal(sent[1].data._revision, 2);
    assert.equal(sent[1].data.notes, 'ab');
    assert.equal(sent[1].data.normalized, true);
    assert.deepEqual(data, { notes: 'ab', normalized: true, _revision: 3 });
    assert.equal(saver.isSaving(1), false);
    assert.equal(saver.isOwnSave(sent[0]._save_id), true);
});

test('conflict blocks queued writes and preserves the draft; a fresh object may save', async () => {
    const { createCharacterSaver } = await load();
    let count = 0;
    const saver = createCharacterSaver(async (id, updates) => {
        count++;
        if (updates.data._revision === 1) throw Object.assign(new Error('conflict'), { status: 409 });
        return { data: { ...updates.data, _revision: 3 } };
    });
    const data = { notes: 'draft', _revision: 1 };
    const first = saver.save(1, { data });
    data.notes = 'continued draft';
    const second = saver.save(1, { data });
    const results = await Promise.allSettled([first, second]);
    assert.deepEqual(results.map(result => result.status), ['rejected', 'rejected']);
    assert.equal(count, 1);
    assert.equal(data.notes, 'continued draft');
    await saver.save(1, { data: { notes: 'fresh', _revision: 2 } });
    assert.equal(count, 2);
});

test('independent copies are not silently assigned the revision from another save', async () => {
    const { createCharacterSaver } = await load();
    const versions = [];
    const saver = createCharacterSaver(async (id, updates) => {
        versions.push(updates.data._revision);
        return { data: { ...updates.data, _revision: versions.length + 1 } };
    });
    await Promise.all([
        saver.save(1, { data: { notes: 'a', _revision: 1 } }),
        saver.save(1, { data: { notes: 'b', _revision: 1 } }),
    ]);
    assert.deepEqual(versions, [1, 1]);
});

test('snapshot registry does not mistake mutable local data for its original base', async () => {
    const { rememberCharacterSnapshots, getCharacterBase } = await load();
    const data = { _character_id: 77, _revision: 1, health: { current: 100 } };
    rememberCharacterSnapshots({ updates: { data } });
    data.health.current = 50;
    rememberCharacterSnapshots(data);
    assert.equal(getCharacterBase(77, data).health.current, 100);
});

test('merge preserves pending deletions, arrays and independent server fields', async () => {
    const { mergeLocalChanges } = await load();
    const result = mergeLocalChanges(
        { _revision: 1, inventory: [1, 2], remove: 1, health: { hp: 10 } },
        { _revision: 1, inventory: [2], health: { hp: 10 } },
        { _revision: 2, inventory: [1, 2], remove: 1, health: { hp: 9, effect: true } },
    );
    assert.deepEqual(result, { _revision: 2, inventory: [2], health: { hp: 9, effect: true } });
});

test('unchanged hidden fields cannot overwrite new server health', async () => {
    const { consumeChangedSheetInputs } = await load();
    const hp = { getAttribute: () => 'health.current', value: '100', defaultValue: '100' };
    const notes = { getAttribute: () => 'notes', value: 'new note', defaultValue: '' };
    const form = { querySelectorAll: () => [hp, notes] };
    assert.deepEqual(consumeChangedSheetInputs(form), [notes]);
    assert.deepEqual(consumeChangedSheetInputs(form), []);
    hp.value = '50';
    assert.deepEqual(consumeChangedSheetInputs(form), [hp]);
    assert.deepEqual(consumeChangedSheetInputs(form), []);
});

test('checkbox and select edits are consumed once, including implicit first option', async () => {
    const { consumeChangedSheetInputs } = await load();
    const check = { getAttribute: () => 'checked', type: 'checkbox', checked: true, defaultChecked: false };
    const select = { getAttribute: () => 'selection', tagName: 'SELECT', options: [
        { selected: true, defaultSelected: false }, { selected: false, defaultSelected: false },
    ] };
    const form = { querySelectorAll: () => [check, select] };
    assert.deepEqual(consumeChangedSheetInputs(form), [check]);
    select.options[0].selected = false;
    select.options[1].selected = true;
    assert.deepEqual(consumeChangedSheetInputs(form), [select]);
    assert.deepEqual(consumeChangedSheetInputs(form), []);
});
