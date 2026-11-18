const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../app/static/js/deferredCombatActions.js'), 'utf8');
const load = () => import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const state = () => ({ status: 'active', current_character: { location_character_id: 7,
    deferred_action: { id: 'saved', action_key: 'attack', status: 'ready', server_executable: true } } });

test('после оплаты атаки уведомление показывает попадание, промах, урон и последствия', async () => {
    const { createDeferredActionResumer } = await load();
    const notices = [];
    const resume = createDeferredActionResumer({
        send: async () => ({ attack: { damage_total: 12, results: [
            { roll: 18, difficulty: 12, hit: true, bleedings: [{ stage: 'severe' }], additional_traumas: [{ roll: 13 }] },
            { roll: 6, difficulty: 12, hit: false },
        ] } }),
        canResume: () => true,
        notify: (text, type) => notices.push([text, type]),
    });
    await resume(state());
    assert.deepEqual(notices, [['1: d20 18, СЛ 12 — попадание · 2: d20 6, СЛ 12 — промах. Итоговый урон: 12. Кровотечения: 1. Доп. травмы: 1.', 'success']]);
});

test('провал Воли после оплаты показывает результат и не сообщает об успешной атаке', async () => {
    const { createDeferredActionResumer } = await load();
    const notices = [];
    const fresh = { status: 'active', current_character: { deferred_action: null } };
    const resume = createDeferredActionResumer({
        send: async () => ({ state: fresh, narrative_action: { blocked_by_will: true, action_points: 2,
            checks: [{ success: true, total: 16, difficulty: 15 }, { success: false, total: 9, difficulty: 15 }] } }),
        cancel: async () => assert.fail('Оплаченная попытка уже завершена'),
        applyState: value => assert.equal(value, fresh), canResume: () => true,
        notify: (message, type) => notices.push([message, type]),
    });
    await resume(state());
    assert.deepEqual(notices, [['Проверка Воли провалена (9 против СЛ 15). Атака сорвалась. Потрачено 2 ОД.', 'error']]);
});

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
    assert.equal(cancelled,1);
    assert.deepEqual(notices, ['Длительное действие отменено: Changed injury']);
});

test('оплаченный расходник сохраняется при сбое сети и завершается при следующем обновлении боя', async () => {
    const { createDeferredActionResumer } = await load();
    const ready = state();
    Object.assign(ready.current_character.deferred_action, {
        action_key: 'consumable_use', server_executable: false, client_executable: true,
    });
    let calls = 0;
    const resume = createDeferredActionResumer({
        canResume: () => true,
        cancel: async () => assert.fail('Оплаченное действие нельзя отменять при сбое сети'),
        async resumeConsumable() {
            if (++calls === 1) throw new TypeError('Failed to fetch');
            return null;
        },
        notify() {},
    });
    await resume(ready);
    await resume(ready);
    await resume(ready);
    assert.equal(calls, 2);
});

test('освобождение инвентаря обрабатывается, пока предыдущая попытка сообщает о блокировке', async () => {
    const { createDeferredActionResumer } = await load();
    const ready = state();
    Object.assign(ready.current_character.deferred_action, {
        action_key: 'consumable_use', server_executable: false, client_executable: true,
    });
    let calls = 0, release;
    const busy = new Promise(resolve => { release = resolve; });
    const resume = createDeferredActionResumer({
        canResume: () => true,
        async resumeConsumable() {
            if (++calls === 1) return busy;
            return null;
        },
        notify() {},
    });
    const first = resume(ready);
    await resume(ready);
    release(false);
    await first;
    assert.equal(calls, 2);
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
