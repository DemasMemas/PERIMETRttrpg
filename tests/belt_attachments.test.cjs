const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../app/static/js/characterSheet.js'),'utf8');
test('снятие и повторное надевание пояса сохраняет физические инструменты и оплаченные слоты',async()=>{
    const template={id:7,name:'Пояс',category:'belt',weight:1,volume:2,attributes:{max_pouches:2}};
    const tool={id:'tool',slotType:'belt_tool',uses:17,maxUses:50};
    const belt={id:'belt',category:'belt',templateId:7,quantity:1,installedModules:[tool],unlockedModificationSlots:2,
        modifications:[{ruleId:'armor-g61'}],attributes:{custom:'сохранить'}};
    const data={equipment:{belt},inventory:{backpack:[],pockets:[]}};
    const context=vm.createContext({window:{},currentCharacterData:data,structuredClone,
        loadTemplatesForLobby:async()=>[template],createItemFromTemplate:()=>({}),
        getItemByPath:keys=>keys.reduce((value,key)=>value[key],data),
        removeItemByPath:keys=>{keys.slice(0,-1).reduce((value,key)=>value[key],data).splice(keys.at(-1),1);return true;},
        generateItemId:()=> 'split',renderEquipmentTab:()=>{},renderInventoryTab:()=>{},
        scheduleAutoSave:()=>{},forceSyncCharacter:()=>{},showNotification:()=>{},
    });
    const equip=source.indexOf('window.equipBeltFromInventory ='),unequip=source.indexOf('window.unequipBelt =');
    vm.runInContext(source.slice(equip,source.indexOf('window.equipVestFromInventory =',equip))
        +source.slice(unequip,source.indexOf('window.unequipVest =',unequip)),context);
    await context.window.unequipBelt();assert.equal(data.inventory.backpack[0].installedModules[0].uses,17);
    await context.window.equipBeltFromInventory(['inventory','backpack',0]);
    assert.equal(data.inventory.backpack.length,0);assert.equal(data.equipment.belt.id,'belt');
    assert.equal(data.equipment.belt.installedModules[0].id,'tool');assert.equal(data.equipment.belt.unlockedModificationSlots,2);
    assert.equal(data.equipment.belt.attributes.custom,'сохранить');
});

test('повторный щелчок крепления не открывает второй выбор и ответ не подменяет другой лист',async()=>{
    const pending={},counts={choices:0,calls:0};
    const context=vm.createContext({medicalOperationInProgress:false,currentCharacterCanEdit:true,currentCharacterId:1,
        currentCharacterData:{_revision:1,equipment:{belt:{id:'belt'}}},medicalRemoteUpdate:null,sheetSaveBlocked:false,
        structuredClone,forceSyncCharacter:async()=>{},Server:{waitForCharacterSave:async()=>{},operateBeltAttachment:async()=>{
            counts.calls++;return new Promise(resolve=>pending.request=resolve);}},
        collectInventoryEntries:()=>[{item:{name:'Инструмент'},path:['inventory','pockets',0]}],getInventoryItemCategory:()=> 'tool',
        chooseConsumableApplication:()=>{counts.choices++;return new Promise(resolve=>pending.choice=resolve);},
        renderEquipmentTab:async()=>assert.fail('Другой лист нельзя перерисовывать'),renderInventoryTab:()=>{},
        showNotification:message=>assert.fail(message),refreshSavedSheet:async()=>{},
    });
    const mounts = source.slice(source.indexOf('const beltDeviceMounts ='), source.indexOf('function isBeltRope('));
    vm.runInContext(mounts + source.slice(source.indexOf('async function changeBeltTool('),source.indexOf('async function changeAutoInjector(')),context);
    const flush=()=>new Promise(resolve=>setImmediate(resolve)),first=context.changeBeltTool('mount');await flush();
    await context.changeBeltTool('mount');assert.equal(counts.choices,1);
    pending.choice({source:{path:['inventory','pockets',0],snapshot:{}}});await flush();assert.equal(counts.calls,1);
    const other={name:'Другой'};context.currentCharacterId=2;context.currentCharacterData=other;
    pending.request({data:{name:'Первый'}});await first;
    assert.equal(context.currentCharacterData,other);assert.equal(context.medicalOperationInProgress,false);
});
