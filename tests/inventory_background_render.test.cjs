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

for(const state of ['closed','replaced'])test('Ожидающий каталог не обновляет '+state+' вкладку',async()=>{
    let resolveTemplates,writes=0;
    const old={set innerHTML(value){writes++;}},replacement={set innerHTML(value){writes++;}};
    let mounted=old;
    const render=renderer({document:{getElementById:()=>mounted},normalizeCharacterAmmoSlots(){},
        pocketCapacity:()=>10,getAllItemTemplates:()=>new Promise(resolve=>{resolveTemplates=resolve;})});
    const pending=render({inventory:{pockets:[],backpack:[]},equipment:{}});
    assert.equal(typeof resolveTemplates,'function');
    mounted=state==='closed'?null:replacement;
    resolveTemplates([]);
    await pending;
    assert.equal(writes,0);
});
