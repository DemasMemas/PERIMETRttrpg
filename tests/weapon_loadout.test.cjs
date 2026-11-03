const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../app/static/js/characterSheet.js'),'utf8');
const extract=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));
test('слоты восстанавливаются у старого шаблона, ограничения ведущего сохраняются',()=>{
    const context=vm.createContext({});
    vm.runInContext(extract('function weaponAttachmentSlots(', '\nasync function renderWeapons('),context);
    const slots=t=>JSON.parse(JSON.stringify(context.weaponAttachmentSlots(t))).map(s=>s.type);
    assert.deepEqual(slots({subcategory:'Штурмовые винтовки и карабины'}),['scope','barrel','handguard']);
    assert.deepEqual(slots({subcategory:'Гранатометы'}),['scope','handguard']);
    assert.deepEqual(slots({attributes:{slots:[]}}),[]);
    assert.deepEqual(slots({attributes:{slots:[{type:'scope'}]}}),['scope']);
});
test('снятие и надевание сохраняют экземпляр, размеры, вложенные модули и боевое состояние',async()=>{
    const template={id:5,name:'АС 19-Б10',category:'weapon',weight:4,volume:8,attributes:{max_durability:100}};
    const original={id:'owned-rifle',templateId:5,name:template.name,model:template.name,category:'weapon',weight:3.5,volume:7,
        durability:53,maxDurability:90,accuracy:0,noise:0,ergonomics:0,_combatDurabilityLoss:7,
        installedModules:[{id:'launcher',category:'weapon_module',slotType:'handguard',weight:1,attributes:{type:'grenade_launcher'},
            loaded:true,loadedGrenade:{id:'round',attributes:{ammo_variant:'smoke'}},durability:19}],
        modifications:[{name:'Переделка'}],fixedAmmo:[],ammo:2,requiresManualCycle:true,jams:[{kind:'misfire'}]};
    const data={weapons:[structuredClone(original)],inventory:{backpack:[]}};
    const context=vm.createContext({window:{},currentCharacterData:data,structuredClone,
        loadTemplatesForLobby:async()=>[template],createItemFromTemplate:t=>({id:'wrong-new-id',...t}),
        generateItemId:()=> 'new-id',showNotification:()=>{},renderEquipmentTab:()=>{},renderInventoryTab:()=>{},
        scheduleAutoSave:()=>{},forceSyncCharacter:()=>{},
        getItemByPath:()=>data.inventory.backpack[0],removeItemByPath:()=>{data.inventory.backpack.splice(0,1);return true}});
    vm.runInContext(extract('window.unequipWeapon =', '\nwindow.unequipBelt ='),context);
    vm.runInContext(extract('window.equipWeaponFromInventory =','\nwindow.equipMeleeWeaponFromInventory ='),context);
    await context.window.unequipWeapon(0);
    assert.equal(data.weapons.length,0);
    assert.equal(data.inventory.backpack[0].id,original.id);
    assert.equal(data.inventory.backpack[0].volume,7);
    assert.deepEqual(data.inventory.backpack[0].installedModules,original.installedModules);
    await context.window.equipWeaponFromInventory(['inventory','backpack',0]);
    assert.equal(data.inventory.backpack.length,0);
    for(const key of ['id','weight','volume','accuracy','noise','ergonomics','durability','maxDurability','_combatDurabilityLoss','requiresManualCycle'])
        assert.equal(data.weapons[0][key],original[key],key);
    for(const key of ['installedModules','modifications','jams'])assert.deepEqual(JSON.parse(JSON.stringify(data.weapons[0][key])),original[key],key);
});
