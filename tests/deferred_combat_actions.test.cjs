const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../app/static/js/deferredCombatActions.js'), 'utf8');
const load = () => import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const state = () => ({ status: 'active', current_character: { location_character_id: 7,
    deferred_action: { id: 'saved', action_key: 'attack', status: 'ready', server_executable: true } } });

test('fresh client resumes by saved ID without a local map, target or weapon parameters', async () => {
    const { createDeferredActionResumer } = await load();
    const sent = [];
    const removed=[];
    const resume = createDeferredActionResumer({ send: async payload => sent.push(payload),
        cancel:async payload=>removed.push(payload),canResume: () => true, notify() {} });
    await resume(state());
    await resume(state());
    assert.deepEqual(sent, [{ location_character_id: 7, action_key: 'attack', resume_pending_action_id: 'saved' }]);
    assert.deepEqual(removed,[{location_character_id:7,action_id:'saved'}]);
});

test('repeated socket events and manual clicks do not start simultaneous requests', async () => {
    const { createDeferredActionResumer } = await load();
    let release, calls = 0;
    const gate = new Promise(resolve => { release = resolve; });
    const resume = createDeferredActionResumer({ send: async () => { calls++; await gate; }, canResume: () => true, notify() {} });
    const pending = resume(state());
    await resume(state(), { force: true });
    assert.equal(calls, 1);
    release();
    await pending;
});

test('неудачное действие отменяется автоматически без повторного исполнения', async () => {
    const { createDeferredActionResumer } = await load();
    let calls = 0, notices = 0;
    const removed=[],applied=[];
    const fresh={status:'active',current_character:{location_character_id:7,deferred_action:null}};
    const resume = createDeferredActionResumer({ send: async () => { calls++; throw new Error('Target moved'); },
        cancel:async payload=>{removed.push(payload);return fresh;},applyState:s=>applied.push(s),
        canResume: () => true, notify: () => notices++ });
    await resume(state());
    await resume(state());
    assert.equal(calls, 1);
    await resume(state(), { force: true });
    assert.equal(calls, 1);
    assert.equal(notices, 1);
    assert.equal(removed.length,3);
    assert.deepEqual(applied,[fresh,fresh,fresh]);
});

test('observers, unpaid actions, ended combat and client-side reloads do not auto-resume', async () => {
    const { createDeferredActionResumer } = await load();
    let calls = 0, allowed = false;
    let removed=0;
    const resume = createDeferredActionResumer({ send: async () => calls++,cancel:async()=>removed++, canResume: () => allowed, notify() {} });
    await resume(state());
    allowed = true;
    const unpaid = state(); unpaid.current_character.deferred_action.status = 'paying';
    await resume(unpaid);
    const ended = state(); ended.status = 'idle';
    await resume(ended);
    assert.equal(removed,0);
    const reload = state(); reload.current_character.deferred_action.server_executable = false;
    await resume(reload);
    assert.equal(calls, 0);
    assert.equal(removed,1,'старое оплаченное действие без параметров удаляется автоматически');
    const next=state();next.current_character.deferred_action.id='new';
    await resume(next);
    assert.equal(calls, 1);
});

test('medical recovery waits for the initiating item lock and then resumes once', async () => {
    const { createDeferredActionResumer } = await load();
    const medical = state();
    Object.assign(medical.current_character.deferred_action, {
        action_key: 'consumable_use', server_executable: false, client_executable: true,
    });
    let busy = true, calls = 0;
    const notices = [];
    const resume = createDeferredActionResumer({
        send() { throw new Error('Not a native combat action'); }, canResume: () => true,
        cancel:async()=>{},
        async resumeConsumable(id) { assert.equal(id, 'saved'); if (busy) return false; calls++; return true; },
        notify: message => notices.push(message),
    });
    await resume(medical);
    assert.equal(notices.length, 0);
    busy = false;
    await resume(medical);
    await resume(medical);
    assert.equal(calls, 1);
    assert.equal(notices.length, 1);
});

test('неудачное завершение лечения автоматически отменяется без повторной попытки', async () => {
    const { createDeferredActionResumer } = await load();
    const medical = state();
    Object.assign(medical.current_character.deferred_action, {
        action_key: 'consumable_use', server_executable: false, client_executable: true,
    });
    let calls = 0;
    let cancelled=0;
    const notices = [];
    const resume = createDeferredActionResumer({ canResume: () => true,
        cancel:async()=>cancelled++,
        async resumeConsumable() { calls++; throw new Error('Changed injury'); },
        notify: message => notices.push(message),
    });
    await resume(medical);
    await resume(medical);
    assert.equal(calls, 1);
    await resume(medical, { force: true });
    assert.equal(calls, 1);
    assert.equal(cancelled,3);
    assert.deepEqual(notices, ['Длительное действие отменено: Changed injury']);
});

test('гонка завершения в двух клиентах очищает строку без повторного эффекта и ложной ошибки',async()=>{
    const {createDeferredActionResumer}=await load();
    const fresh={status:'active',current_character:{location_character_id:7}};
    let applied,notices=0,cancelled=0;
    const resume=createDeferredActionResumer({canResume:()=>true,
        send:async()=>{throw Object.assign(new Error('Already completed'),{status:409});},
        cancel:async()=>{cancelled++;return fresh;},applyState:s=>{applied=s;},notify:()=>notices++});
    await resume(state());
    assert.equal(cancelled,1);assert.equal(applied,fresh);assert.equal(notices,0);
});
