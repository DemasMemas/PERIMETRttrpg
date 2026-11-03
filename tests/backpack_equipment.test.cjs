const {test}=require('node:test');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../app/static/js/characterSheet.js'),'utf8');
const start=source.indexOf('window.equipBackpackFromInventory ='),end=source.indexOf('\nwindow.unequipBackpack =',start);
assert.ok(start>0&&end>start);
function setup(backpack){
    const data={equipment:{},inventory:{backpack,pockets:[]}};
    const context=vm.createContext({window:{},currentCharacterData:data,
        getItemByPath:keys=>keys.reduce((value,key)=>value[key],data),
        removeItemByPath:keys=>{keys.slice(0,-1).reduce((value,key)=>value[key],data).splice(keys.at(-1),1);return true;},
        generateItemId:()=> 'equipped-bag',showNotification:()=>{},renderInventoryTab:async()=>{},scheduleAutoSave:()=>{},forceSyncCharacter:()=>{},
    });
    vm.runInContext(source.slice(start,end),context);
    return{data,equip:context.window.equipBackpackFromInventory};
}
test('надевание купленного рюкзака сохраняет неразмещённые покупки и собственное содержимое',async()=>{
    const {data,equip}=setup([{id:'ammo',quantity:20},{id:'bag',category:'backpack',contents:[{id:'bandage'}]},{id:'food'}]);
    await equip(['inventory','backpack',1]);
    assert.equal(data.equipment.backpack.id,'bag');
    assert.deepEqual(Array.from(data.inventory.backpack,item=>item.id),['ammo','food','bandage']);
    assert.equal(data.inventory.backpack[0].quantity,20);
    assert.equal(data.equipment.backpack.contents,undefined);
});
test('надевание одного пустого рюкзака из стопки сохраняет остальные экземпляры',async()=>{
    const {data,equip}=setup([{id:'bags',category:'backpack',quantity:2,contents:[]},{id:'ammo',quantity:20}]);
    await equip(['inventory','backpack',0]);
    assert.equal(data.equipment.backpack.quantity,1);
    assert.equal(data.equipment.backpack.id,'equipped-bag');
    assert.equal(data.inventory.backpack[0].id,'bags');
    assert.equal(data.inventory.backpack[0].quantity,1);
    assert.equal(data.inventory.backpack[1].quantity,20);
});
test('перенос в пустые карманы учитывает вместимость после перерисовки',async()=>{
    const data={inventory:{pockets:[],backpack:[{id:'ammo',volume:2,quantity:2}]}};
    const handlers={},notices=[];
    const dropTarget={classList:{add(){},remove(){}},setAttribute(){},addEventListener:(event,fn)=>{handlers[event]=fn;}};
    const context=vm.createContext({currentCharacterData:data,draggedItem:null,draggedItemPath:null,
        getItemByPath:keys=>keys.reduce((value,key)=>value[key],data),
        removeItemByPath:keys=>{keys.slice(0,-1).reduce((value,key)=>value[key],data).splice(keys.at(-1),1);return true;},
        pocketCapacity:()=>10,getTotalVolume:item=>item.volume*item.quantity,
        showNotification:message=>notices.push(message),renderInventoryTab:async()=>{},recalculateInventoryTotals(){},scheduleAutoSave(){},forceSyncCharacter(){},
    });
    const start=source.indexOf('function setupDropTarget('),end=source.indexOf('\nwindow.getAllItemTemplates',start);
    vm.runInContext(source.slice(start,end),context);
    context.setupDropTarget(dropTarget,['inventory','pockets'],data.inventory.pockets);
    const event=()=>({preventDefault(){},stopPropagation(){},dataTransfer:{getData:type=>type==='application/x-inventory-path'?'["inventory","backpack",0]':''}});
    await handlers.drop(event());
    assert.equal(data.inventory.backpack.length,0);
    assert.equal(data.inventory.pockets[0].id,'ammo');
    data.inventory.backpack.push({id:'large',volume:7,quantity:1});
    await handlers.drop(event());
    assert.equal(data.inventory.backpack[0].id,'large');
    assert.equal(data.inventory.pockets.length,1);
    assert.equal(notices.at(-1),'Недостаточно места в контейнере');
});
