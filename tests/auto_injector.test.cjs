const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../app/static/js/characterSheet.js'),'utf8');
function setup(){
    const counts={choices:0,calls:0,renders:0},pending={};
    const data={_revision:1,equipment:{armor:{id:'armor'}}};
    const context=vm.createContext({currentCharacterId:1,currentCharacterData:data,currentCharacterCanEdit:true,
        sheetSaveBlocked:false,medicalOperationInProgress:false,medicalRemoteUpdate:null,
        forceSyncCharacter:async()=>{},Server:{waitForCharacterSave:async()=>{},operateAutoInjector:async()=>{
            counts.calls++;return new Promise(resolve=>pending.request=resolve);}},
        collectInventoryEntries:()=>[{item:{id:'dose',name:'Препарат'},path:['inventory','pockets',0]}],
        chooseConsumableApplication:()=>{counts.choices++;return new Promise(resolve=>pending.choice=resolve);},
        showNotification:()=>{},renderEquipmentTab:async()=>{counts.renders++;},renderInventoryTab:()=>{},refreshSavedSheet:async()=>{},
    });
    vm.runInContext(source.slice(source.indexOf('async function changeAutoInjector('),source.indexOf('\nwindow.rechargeNeckBrace')),context);
    return {context,counts,pending};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
test('повторный щелчок загрузки не открывает второй выбор препарата',async()=>{
    const {context,counts,pending}=setup();const first=context.changeAutoInjector('load');await flush();
    assert.equal(context.medicalOperationInProgress,true);
    await context.changeAutoInjector('load');assert.equal(counts.choices,1);
    pending.choice(null);await first;assert.equal(context.medicalOperationInProgress,false);assert.equal(counts.calls,0);
});
test('завершение загрузки не подменяет данные другого открытого персонажа',async()=>{
    const {context,counts,pending}=setup();const first=context.changeAutoInjector('load');await flush();
    pending.choice({source:{path:['inventory','pockets',0],snapshot:{id:'dose'}}});await flush();assert.equal(counts.calls,1);
    context.currentCharacterId=2;const other={name:'Другой персонаж'};context.currentCharacterData=other;
    pending.request({data:{name:'Исходный персонаж'}});await first;
    assert.equal(context.currentCharacterData,other);assert.equal(counts.renders,0);assert.equal(context.medicalOperationInProgress,false);
});
