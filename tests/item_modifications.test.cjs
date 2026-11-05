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
