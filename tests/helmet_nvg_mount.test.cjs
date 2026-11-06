const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const source=fs.readFileSync(path.join(__dirname,'../app/static/js/characterSheet.js'),'utf8');
const start=source.indexOf('function getItemSlots(item) {');
const end=source.indexOf('\nfunction getEffectiveTorsoProtection(',start);
assert.ok(start>=0&&end>start);
const context=vm.createContext({allTemplatesCache:[],templatesCache:{},currentLobbyId:1});
vm.runInContext(source.slice(start,end),context);

test('крепление ПНВ добавляет слот шлему и не дублирует штатный',()=>{
    const helmet={category:'helmet',attributes:{slots:[{type:'visor',label:'Забрало'}]}};
    assert.deepEqual(Array.from(context.getItemSlots(helmet),slot=>slot.type),['visor']);
    helmet.modifications=[{ruleId:'armor-g25'}];
    assert.deepEqual(Array.from(context.getItemSlots(helmet),slot=>slot.type),['visor','nvg']);
    helmet.attributes.slots.push({type:'nvg',label:'ПНВ'});
    assert.deepEqual(Array.from(context.getItemSlots(helmet),slot=>slot.type),['visor','nvg']);
});
