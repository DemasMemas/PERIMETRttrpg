const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');

async function rules(){
    const module=new vm.SourceTextModule(fs.readFileSync(path.join(__dirname,'../app/static/js/itemModifications.js'),'utf8'));
    await module.link(()=>{throw Error('Неожиданная зависимость');});await module.evaluate();return module.namespace;
}

test('окно забрала показывает установленные улучшения без бесплатного добавления и удаления',()=>{
    const source=fs.readFileSync(path.join(__dirname,'../app/static/js/characterSheet.js'),'utf8');
    const begin=source.indexOf('window.openVisorModificationsModal ='),end=source.indexOf('window.selectWeaponModel =',begin);
    const visor={slotType:'visor',modifications:[{name:'Старая модификация',description:'Сохранена'},
        {ruleId:'armor-g25',name:'Крепление ПНВ',level:1,description:'Крепление ПНВ'}]};
    const helmet={installedModules:[visor]};
    let modal;
    const document={getElementById:()=>null,createElement:()=>({style:{},querySelector:()=>({})}),
        body:{appendChild:element=>{modal=element;}}};
    const context=vm.createContext({window:{},document,JSON,getItemByPath:()=>helmet,
        showNotification:()=>{throw Error('Забрало не найдено');},
        renderRuleModification:mod=>`<strong>${mod.name}</strong>`,
        renderLegacyModification:mod=>`<strong>${mod.name}</strong>`});
    vm.runInContext(source.slice(begin,end),context);
    context.window.openVisorModificationsModal('["equipment","helmet"]','visor');
    assert.match(modal.innerHTML,/Старая модификация/);
    assert.match(modal.innerHTML,/Крепление ПНВ/);
    assert.doesNotMatch(modal.innerHTML,/Добавить модификацию|visor-mod-select|data-mod-index|add-mod-btn/);
    assert.equal(visor.modifications.length,2);
});

test('фиксированные бонусы и дробные значения модификаций доходят до предпросмотра',async()=>{
    const api=await rules();
    const item={modifications:[{name:'Старая заметка',modifiers:{ergonomics:999}},
        {ruleId:'weapon-b29',modifiers:{ergonomics:'=75.0'}},{ruleId:'weapon-c23',modifiers:{ergonomics:7.5}}]};
    assert.equal(api.modificationStat(item,'ergonomics',10),82.5);
    assert.equal(api.modificationStat(item,'accuracy',2),2);
    const equipped={equipment:{armor:{modifications:[{ruleId:'armor-a5',modifiers:{physical:15}}]},
        helmet:{protectionDisabled:true,modifications:[{ruleId:'armor-d5',modifiers:{physical:10}}]}}};
    assert.equal(api.wornModificationStat(equipped,'physical'),15);
});

test('пистолетная очередь и действия в интерфейсе совпадают с установленными правилами',async()=>{
    const api=await rules(),base={supports_burst:false};
    const item={modifications:[{ruleId:'weapon-c24',rules:{pistol_burst:true,pistol_burst_factor:1.5}},
        {ruleId:'weapon-b38',rules:{burst_ap_delta:-1}}]};
    assert.equal(api.modifiedFireProfile(item,base,6).burst_size,6);
    assert.equal(base.supports_burst,false);
    assert.equal(api.modificationRule(item,'burst_ap_delta'),-1);
});

test('предпросмотр зарядки учитывает нулевую стоимость подготовленного улучшенного гранатомёта',async()=>{
    const api=await rules();
    const source=fs.readFileSync(path.join(__dirname,'../app/static/js/characterSheet.js'),'utf8');
    const begin=source.indexOf('function fixedMagazineLoadingPlans('),end=source.indexOf('window.calculateInventoryAccess',begin);
    const context=vm.createContext({currentCharacterData:{skills:{specialized:{grenadeLaunchers:{level:'familiar'}}}},
        ammoLoadingKind:()=> 'loose',traitCount:()=>0,weaponSpecializationKey:()=> 'grenadeLaunchers',
        ammoSourceCount:()=>10,modificationRule:api.modificationRule,wornModificationRule:api.wornModificationRule});
    vm.runInContext(source.slice(begin,end),context);
    const weapon={modifications:[{ruleId:'weapon-c44',rules:{reload_ap_delta:-1}}]};
    assert.equal(context.fixedMagazineLoadingPlans({}, {},1,true,weapon)[0].payments[0].actionPoints,0);
    assert.equal(context.fixedMagazineLoadingPlans({}, {},1,false,weapon)[0].payments[0].actionPoints,1);
    assert.equal(context.fixedMagazineLoadingPlans({}, {},1,true,{})[0].payments[0].actionPoints,1);
});

test('масса на поясе и карманы совпадают с правилами установленных модификаций',async()=>{
    const api=await rules(),source=fs.readFileSync(path.join(__dirname,'../app/static/js/characterSheet.js'),'utf8');
    const begin=source.indexOf('function calculateCarriedWeight('),end=source.indexOf('function getMovementPenaltyBreakdown(',begin);
    const context=vm.createContext({modificationRule:api.modificationRule,getTotalWeight:item=>item.weight||0});
    vm.runInContext(source.slice(begin,end),context);
    const data={inventory:{pockets:[{weight:3}]},equipment:{belt:{pouches:[null,{contents:[{weight:10}]}],
        modifications:[{ruleId:'armor-g58',rules:{belt_weight_reduction:.5}}]},armor:{modifications:[{ruleId:'armor-a30',modifiers:{pocket_cells:15}}]}}};
    assert.equal(context.calculateCarriedWeight(data),8);
    const traitsSource=fs.readFileSync(path.join(__dirname,'../app/static/js/characterTraits.js'),'utf8');
    const traits=await import('data:text/javascript;base64,'+Buffer.from(traitsSource).toString('base64'));
    assert.equal(traits.pocketCapacity(data),25);
    data.equipment.armor.protectionDisabled=true;assert.equal(traits.pocketCapacity(data),10);
    data.inventory.pockets.push({id:'belt-light',weight:2});
    data.equipment.belt.modifications.push({ruleId:'armor-g62',rules:{belt_light_mount:true}});
    data.health={combatMeta:{portableLights:[{id:'belt-light',mount:'belt',on:false}]}};
    context.collectInventoryEntries=()=>[{item:data.inventory.pockets[1],path:['inventory','pockets',1]}];
    assert.equal(context.calculateCarriedWeight(data),9);
    data.equipment.belt.installedModules=[{id:'tool',slotType:'belt_tool',weight:4}];
    data.equipment.belt.modifications.push({ruleId:'armor-g61',rules:{belt_tool_mounts:4}});
    assert.equal(context.calculateCarriedWeight(data),9);
    data.equipment.belt.protectionDisabled=true;assert.equal(context.calculateCarriedWeight(data),19);
    data.equipment.belt.installedModules.push({id:'artifact',slotType:'belt_artifact',weight:2});
    assert.equal(context.calculateCarriedWeight(data),21);
    data.equipment.belt.protectionDisabled=false;assert.equal(context.calculateCarriedWeight(data),10);
});

test('расширенный пояс переносит купленные подсумки без дублирования',async()=>{
    const api=await rules(),source=fs.readFileSync(path.join(__dirname,'../app/static/js/characterSheet.js'),'utf8');
    const begin=source.indexOf('function beltPouchLimit('),end=source.indexOf('window.addBeltModification',begin);
    const pouch={id:'owned-pouch',templateId:7,name:'Подсумок',category:'pouch',quantity:4,capacity:2,contents:[]};
    const currentCharacterData={equipment:{belt:{templateId:1,pouches:[]}},inventory:{backpack:[pouch]}},notifications=[];
    const context=vm.createContext({modificationRule:api.modificationRule,
        getInventoryItemTemplate:()=>({attributes:{max_pouches:2}}),currentCharacterData,structuredClone,
        window:{},document:{getElementById:()=>null},generateItemId:()=>`split-${currentCharacterData.equipment.belt.pouches.length}`,
        collectInventoryEntries:()=>[],getItemByPath:path=>currentCharacterData.inventory.backpack[path[2]],
        removeItemByPath:path=>Boolean(currentCharacterData.inventory.backpack.splice(path[2],1).length),
        addToBackpack:item=>currentCharacterData.inventory.backpack.push(item),returnPouchContents:()=>{},
        updateDataFromFields:()=>{},renderInventoryTab:()=>{},scheduleAutoSave:()=>{},forceSyncCharacter:()=>{},showNotification:message=>notifications.push(message)});
    vm.runInContext(source.slice(begin,end),context);
    const belt=currentCharacterData.equipment.belt;
    assert.equal(context.beltPouchLimit(belt),2);
    for(let i=0;i<3;i++)context.window.mountPouchFromInventory('belt',['inventory','backpack',0],'owned-pouch');
    assert.equal(belt.pouches.length,2);
    assert.equal(notifications.length,1);
    assert.equal(pouch.quantity,2);
    assert.deepEqual(belt.pouches.map(item=>item.id),['split-0','split-1']);
    belt.modifications=[{ruleId:'armor-g57',rules:{extra_belt_pouches:2}}];
    assert.equal(context.beltPouchLimit(belt),4);
    for(let i=0;i<2;i++)context.window.mountPouchFromInventory('belt',['inventory','backpack',0],'owned-pouch');
    assert.equal(belt.pouches.length,4);
    assert.equal(currentCharacterData.inventory.backpack.length,0);
    assert.equal(belt.pouches[3].id,'owned-pouch');
    context.window.removeBeltPouch(3);
    assert.equal(currentCharacterData.inventory.backpack[0].id,'owned-pouch');
    belt.pouches.length=2;
    belt.extraStoredItem={name:'Шлем',category:'helmet'};
    assert.equal(context.beltPouchLimit(belt),2);
    context.window.mountPouchFromInventory('belt',['inventory','backpack',0],'owned-pouch');
    assert.equal(belt.pouches.length,2);
});

test('пересчёт инвентаря использует общую массу с креплениями и разгрузочным поясом',async()=>{
    const api=await rules(),source=fs.readFileSync(path.join(__dirname,'../app/static/js/characterSheet.js'),'utf8');
    const data={inventory:{pockets:[{weight:3}],backpack:[]},equipment:{belt:{pouches:[{contents:[{weight:10}]}],
        installedModules:[{slotType:'belt_artifact',weight:2},{slotType:'belt_tool',weight:4}],
        modifications:[{ruleId:'armor-g58',rules:{belt_weight_reduction:.5}},{ruleId:'armor-g61',rules:{belt_tool_mounts:4}}]}}};
    const elements=new Map(),context=vm.createContext({currentCharacterData:data,modificationRule:api.modificationRule,
        getTotalWeight:item=>item.weight||0,getTotalVolume:()=>0,migrateOldItemToNew:item=>item,pocketCapacity:()=>10,
        getMovementPenaltyBreakdown:(_,weight)=>({total:0,weightPenalty:0,weightPerPenalty:5}),
        document:{getElementById:id=>{if(!elements.has(id))elements.set(id,{style:{}});return elements.get(id);}}});
    const begin=source.indexOf('function calculateCarriedWeight('),end=source.indexOf('function getMovementPenaltyBreakdown(',begin);
    const recalc=source.indexOf('function recalculateInventoryTotals() {');
    vm.runInContext(source.slice(begin,end)+source.slice(recalc,source.indexOf('window.selectPocketItem =',recalc)),context);
    context.recalculateInventoryTotals();assert.equal(elements.get('total-weight-display').textContent,'9.0');
    data.equipment.belt.protectionDisabled=true;context.recalculateInventoryTotals();
    assert.equal(elements.get('total-weight-display').textContent,'19.0');
});

test('дополнительное крепление пояса сохраняет экземпляр шлема при снятии',async()=>{
    const api=await rules(),source=fs.readFileSync(path.join(__dirname,'../app/static/js/characterSheet.js'),'utf8');
    const helmet={id:'helmet-1',name:'Шлем',category:'helmet',templateId:7,installedModules:[{id:'nvg-1'}],
        modifications:[{ruleId:'armor-g25',rules:{nvg_mount:true}}]};
    const belt={templateId:5,maxPouches:2,pouches:[{},{}],storedItem:{name:'Противогаз'},
        modifications:[{ruleId:'armor-g57',rules:{extra_belt_pouches:2}}]};
    const currentCharacterData={equipment:{belt},inventory:{backpack:[helmet]}};
    const context=vm.createContext({window:{},currentCharacterData,modificationRule:api.modificationRule,structuredClone,
        getItemByPath:()=>currentCharacterData.inventory.backpack[0],removeItemByPath:()=>Boolean(currentCharacterData.inventory.backpack.splice(0,1).length),
        getInventoryItemTemplate:()=>({attributes:{max_pouches:2}}),restoreItemToPath:item=>{currentCharacterData.inventory.backpack.push(item);return true;},
        renderInventoryTab:()=>{},scheduleAutoSave:()=>{},forceSyncCharacter:()=>{},showNotification:()=>{}});
    let begin=source.indexOf('window.equipToBeltFromInventory ='),end=source.indexOf('window.equipHeadphonesFromInventory',begin);
    vm.runInContext(source.slice(begin,end),context);
    begin=source.indexOf('window.unequipFromBelt =');end=source.indexOf('window.unequipArmor',begin);
    vm.runInContext(source.slice(begin,end),context);
    await context.window.equipToBeltFromInventory(['inventory','backpack',0]);
    assert.equal(belt.extraStoredItem.id,'helmet-1');
    assert.equal(currentCharacterData.inventory.backpack.length,0);
    await context.window.unequipFromBelt('extra');
    assert.equal(currentCharacterData.inventory.backpack[0].id,'helmet-1');
    assert.equal(currentCharacterData.inventory.backpack[0].installedModules[0].id,'nvg-1');
    assert.equal(currentCharacterData.inventory.backpack[0].modifications[0].ruleId,'armor-g25');
});

test('система распределения веса экзоскелета совпадает с серверным правилом',async()=>{
    const api=await rules(),source=fs.readFileSync(path.join(__dirname,'../app/static/js/characterSheet.js'),'utf8');
    const begin=source.indexOf('function getExoskeletonPowerProfile('),end=source.indexOf('function applyModifier(',begin);
    const context=vm.createContext({modificationRule:api.modificationRule,modificationStat:api.modificationStat,
        wornModificationStat:()=>0,getWeightPerMovementPenalty:()=>5,getConsumableStatValue:()=>0,
        passiveTraits:()=>({weightMultiplier:1,movementPenalty:0}),prostheticLimb:()=>null,
        getIntoxicationProfile:()=>({movement:0})});
    vm.runInContext(source.slice(begin,end),context);
    const armor={name:'Экзоскелет',movementPenalty:5,
        installedModules:[{slotType:'exoskeleton_battery',attributes:{remaining_days:0}}],
        modifications:[{ruleId:'armor-d27',rules:{unpowered_weight_penalty_suppression:true}}]};
    const data={equipment:{armor},inventory:{}};
    assert.equal(context.getMovementPenaltyBreakdown(data,5).weightPenalty,0);
    assert.equal(context.getMovementPenaltyBreakdown(data,5).unpoweredWeightUpgrade,true);
    data._artifact_profile={movement_penalty:2};
    assert.equal(context.getMovementPenaltyBreakdown(data,5).artifactPenalty,2);
    assert.equal(context.getMovementPenaltyBreakdown(data,5).total,7);
    armor.installedModules=[];
    assert.equal(context.getMovementPenaltyBreakdown(data,5).weightPenalty,1);
    armor.installedModules=[{slotType:'exoskeleton_battery',attributes:{remaining_days:0}}];
    armor.protectionDisabled=true;
    assert.equal(context.getMovementPenaltyBreakdown(data,5).weightPenalty,1);
});

test('фиксированный штраф экзоскелета равен трём с заряженной и разряженной батареей',async()=>{
    const api=await rules(),source=fs.readFileSync(path.join(__dirname,'../app/static/js/characterSheet.js'),'utf8');
    const begin=source.indexOf('function getExoskeletonPowerProfile('),end=source.indexOf('function applyModifier(',begin);
    const context=vm.createContext({modificationRule:api.modificationRule,modificationStat:api.modificationStat,
        wornModificationStat:()=>0,getWeightPerMovementPenalty:()=>5,getConsumableStatValue:()=>0,
        passiveTraits:()=>({weightMultiplier:1,movementPenalty:0}),prostheticLimb:()=>null,
        getIntoxicationProfile:()=>({movement:0})});
    vm.runInContext(source.slice(begin,end),context);
    const armor={name:'Экзоскелет',movementPenalty:5,
        installedModules:[{slotType:'exoskeleton_battery',attributes:{remaining_days:1}}],
        modifications:[{ruleId:'armor-d28',modifiers:{movement_penalty:'=3'}}]};
    const data={equipment:{armor},inventory:{}};
    assert.equal(context.getMovementPenaltyBreakdown(data,0).armorPenalty,3);
    armor.installedModules[0].attributes.remaining_days=0;
    assert.equal(context.getMovementPenaltyBreakdown(data,0).armorPenalty,3);
    armor.modifications=[];
    assert.equal(context.getMovementPenaltyBreakdown(data,0).armorPenalty,5);
});

test('модификация брони добавляет реальный патрон при зарядке, ограничиваясь местом',async()=>{
    const api=await rules(),source=fs.readFileSync(path.join(__dirname,'../app/static/js/characterSheet.js'),'utf8');
    const begin=source.indexOf('function fixedMagazineLoadingPlans('),end=source.indexOf('window.calculateInventoryAccess',begin);
    const context=vm.createContext({currentCharacterData:{skills:{specialized:{shotguns:{level:'professional'}}},
        equipment:{armor:{modifications:[{ruleId:'armor-a34',rules:{fixed_reload_extra:1}}]}}},
        ammoLoadingKind:()=> 'loose',traitCount:()=>0,weaponSpecializationKey:()=> 'shotguns',
        ammoSourceCount:()=>10,modificationRule:api.modificationRule,wornModificationRule:api.wornModificationRule});
    vm.runInContext(source.slice(begin,end),context);
    assert.equal(context.fixedMagazineLoadingPlans({}, {},5,true,{})[0].quantity,3);
    assert.equal(context.fixedMagazineLoadingPlans({}, {},1,true,{})[0].quantity,1);
    context.currentCharacterData.equipment.armor.protectionDisabled=true;
    assert.equal(context.fixedMagazineLoadingPlans({}, {},5,true,{})[0].quantity,2);
});
