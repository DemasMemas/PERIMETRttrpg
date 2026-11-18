const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../app/static/js/characterSheet.js'),'utf8');
const start=source.indexOf('async function renderInventoryTab(data) {');
const finish=source.indexOf('\nfunction getItemByPath(',start);
assert.ok(start>=0&&finish>start);

function renderer(globals){
    const context=vm.createContext(globals);
    vm.runInContext(source.slice(start,finish)+'\nglobalThis.render=renderInventoryTab;',context);
    return context.render;
}

test('a row accepts a refreshed copy of its item and rejects a replacement at the same index', () => {
    const handlers = new Map();
    const context = vm.createContext({
        currentCharacterId: 1,
        currentCharacterData: { pockets: [{ id: 'selected' }] },
        getItemByPath(path) {
            return context.currentCharacterData.pockets[path[1]];
        },
    });
    vm.runInContext(source.slice(source.indexOf('function sameInventoryItem('),
        source.indexOf('function renderBackpackItem(')), context);
    context.bindInventoryRow({
        addEventListener(name, callback, capture) {
            assert.equal(capture, true);
            handlers.set(name, callback);
        },
    }, ['pockets', 0]);

    for (const callback of handlers.values()) {
        const actions = [];
        const event = {
            preventDefault: () => actions.push('prevent'),
            stopImmediatePropagation: () => actions.push('stop'),
        };
        context.currentCharacterData = { pockets: [{ id: 'selected' }] };
        callback(event);
        assert.deepEqual(actions, []);
        context.currentCharacterData = { pockets: [{ id: 'next' }] };
        callback(event);
        assert.deepEqual(actions, ['prevent', 'stop']);
    }
    assert.deepEqual([...handlers.keys()], ['click', 'input', 'change', 'dragstart']);
});

test('Фоновое действие без открытого листа не запускает рендер инвентаря',async()=>{
    let loads=0;
    const data={inventory:{pockets:[],backpack:[]}};
    const render=renderer({document:{getElementById:()=>null},getAllItemTemplates:()=>{
        loads++;throw Error('Без вкладки каталог не нужен');
    }});
    await render(data);
    assert.equal(loads,0);
    assert.deepEqual(data,{inventory:{pockets:[],backpack:[]}});
});

for(const state of ['closed','replaced','same DOM after closing','same DOM with a different character'])test('Ожидающий каталог не обновляет '+state+' вкладку',async()=>{
    let resolveTemplates,writes=0;
    const old={set innerHTML(value){writes++;}},replacement={set innerHTML(value){writes++;}};
    let mounted=old;
    const data={inventory:{pockets:[],backpack:[]},equipment:{}};
    const globals={currentCharacterId:1,currentCharacterData:data,
        document:{getElementById:()=>mounted},normalizeCharacterAmmoSlots(){},
        pocketCapacity:()=>10,getAllItemTemplates:()=>new Promise(resolve=>{resolveTemplates=resolve;})};
    const pending=renderer(globals)(data);
    assert.equal(typeof resolveTemplates,'function');
    if(state==='closed') mounted=null;
    if(state==='replaced') mounted=replacement;
    if(state==='same DOM after closing') globals.currentCharacterId=globals.currentCharacterData=null;
    if(state==='same DOM with a different character') globals.currentCharacterId=2;
    resolveTemplates([]);
    await pending;
    assert.equal(writes,0);
});

test('удаление перестраивает действия всех оставшихся строк до сохранения', async () => {
    const items = [{ id: 'removed' }, { id: 'tourniquet', quantity: 1 }, { id: 'alpha', quantity: 1 }];
    const data = { inventory: { pockets: items } };
    const rendered = [];
    const container = { inert: false, isConnected: true, innerHTML: 'old rows' };
    const context = vm.createContext({
        window: {}, document: { querySelector: () => ({ parentElement: container }) },
        currentCharacterId: 1, currentCharacterData: data, currentCharacterCanEdit: true,
        medicalOperationInProgress: false, sheetSaveBlocked: false, allTemplatesCache: [],
        renderBackpackItem(item, index, parentPath) {
            assert.ok(container.inert);
            rendered.push({ id: item.id, path: [...parentPath, index] });
        },
        setupDropTarget() {}, recalculateInventoryTotals() {}, scheduleAutoSave() {},
        forceSyncCharacter() { assert.equal(rendered.length, 2); },
    });
    vm.runInContext([
        source.slice(source.indexOf('function getItemByPath('), source.indexOf('\nfunction removeItemByPath(')),
        source.slice(source.indexOf('window.removeBackpackItemAtPath ='), source.indexOf('\nfunction getBackpackItemAtPath(')),
        source.slice(source.indexOf('async function rerenderContainer('), source.indexOf('// ========== 10. UI-ФУНКЦИИ')),
    ].join('\n'), context);
    await context.window.removeBackpackItemAtPath('inventory,pockets,0');
    assert.deepEqual(rendered, [
        { id: 'tourniquet', path: ['inventory', 'pockets', 0] },
        { id: 'alpha', path: ['inventory', 'pockets', 1] },
    ]);
    assert.equal(container.inert, false);
});
