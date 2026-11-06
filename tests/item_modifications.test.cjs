const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');

async function rules(){
    const module=new vm.SourceTextModule(fs.readFileSync(path.join(__dirname,'../app/static/js/itemModifications.js'),'utf8'));
    await module.link(()=>{throw Error('Неожиданная зависимость');});await module.evaluate();return module.namespace;
}

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
});

test('расширенный пояс открывает два дополнительных места для подсумков',async()=>{
    const api=await rules(),source=fs.readFileSync(path.join(__dirname,'../app/static/js/characterSheet.js'),'utf8');
    const begin=source.indexOf('function beltPouchLimit('),end=source.indexOf('window.removeBeltPouch',begin);
    const currentCharacterData={equipment:{belt:{templateId:1,pouches:[]}}},notifications=[];
    const context=vm.createContext({modificationRule:api.modificationRule,
        getInventoryItemTemplate:()=>({attributes:{max_pouches:2}}),currentCharacterData,
        window:{},renderInventoryTab:()=>{},scheduleAutoSave:()=>{},showNotification:message=>notifications.push(message)});
    vm.runInContext(source.slice(begin,end),context);
    const belt=currentCharacterData.equipment.belt;
    assert.equal(context.beltPouchLimit(belt),2);
    for(let i=0;i<3;i++)context.window.addBeltPouch();
    assert.equal(belt.pouches.length,2);
    assert.equal(notifications.length,1);
    belt.modifications=[{ruleId:'armor-g57',rules:{extra_belt_pouches:2}}];
    assert.equal(context.beltPouchLimit(belt),4);
    for(let i=0;i<3;i++)context.window.addBeltPouch();
    assert.equal(belt.pouches.length,4);
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
