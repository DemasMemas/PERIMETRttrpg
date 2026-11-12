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
const containerKind = source.match(/^function artifactContainerKind\([\s\S]*?^\}/m)[0];
vm.runInContext(containerKind + '\n' + source.slice(start,end),context);

test('крепление ПНВ добавляет слот шлему и не дублирует штатный',()=>{
    const helmet={category:'helmet',attributes:{slots:[{type:'visor',label:'Забрало'}]}};
    assert.deepEqual(Array.from(context.getItemSlots(helmet),slot=>slot.type),['visor']);
    helmet.modifications=[{ruleId:'armor-g25'}];
    assert.deepEqual(Array.from(context.getItemSlots(helmet),slot=>slot.type),['visor','nvg']);
    helmet.attributes.slots.push({type:'nvg',label:'ПНВ'});
    assert.deepEqual(Array.from(context.getItemSlots(helmet),slot=>slot.type),['visor','nvg']);
});

test('крепление забрала даёт одно место, старые приборы можно снять',()=>{
    const visor={slotType:'visor',attributes:{slots:[{type:'nvg'},{type:'light'}]}};
    const slots=()=>Array.from(context.getItemSlots(visor),slot=>slot.type);
    assert.deepEqual(slots(),[]);
    visor.modifications=[{ruleId:'armor-g18'}];
    assert.deepEqual(slots(),['visor_device']);
    visor.installedModules=[{slotType:'nvg'}];
    assert.deepEqual(slots(),['nvg']);
    visor.installedModules.push({slotType:'light'});
    assert.deepEqual(slots(),['nvg','light']);
    visor.installedModules=[];
    visor.protectionDisabled=true;
    assert.deepEqual(slots(),[]);
});

test('вложенный ПНВ учитывается листом только на надетом исправном забрале',()=>{
    const power=fs.readFileSync(path.join(__dirname,'../app/static/js/portableLights.js'),'utf8');
    const start=power.indexOf('export function usesNightVision('),end=power.indexOf('export async function renderPortableLights(',start);
    const runtime=vm.createContext({});vm.runInContext(power.slice(start,end).replace('export ',''),runtime);
    const nvg={slotType:'nvg',lightPower:{uses_left:4}},visor={slotType:'visor',installedModules:[nvg]};
    const data={equipment:{helmet:{installedModules:[visor]}}};
    assert.ok(runtime.usesNightVision(data));
    visor.protectionDisabled=true;assert.ok(!runtime.usesNightVision(data));
    visor.protectionDisabled=false;nvg.lightPower.uses_left=0;assert.ok(!runtime.usesNightVision(data));
    nvg.lightPower.uses_left=4;data.equipment.helmet.installedModules=[];
    data.inventory={backpack:[visor]};assert.ok(!runtime.usesNightVision(data));
});
