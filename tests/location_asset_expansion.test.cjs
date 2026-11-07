const {test}=require('node:test'),assert=require('node:assert/strict');
const {modules,fixture}=require('./location_furniture_fixture.cjs');
test('обычные станки размещаются как объекты сцены с проверяемым назначением',async()=>{
    const {C}=await modules();
    const stations=C.LOCATION_ASSETS.filter(asset=>asset.group==='Станки');
    assert.equal(stations.length,7);
    assert.deepEqual(new Set(stations.filter(asset=>asset.properties.craft_workbench).map(asset=>asset.properties.craft_workbench)),
        new Set(['Самогонный Аппарат','Печь','Станок Оружейника','Станок Бронника',
            'Смесительный Станок','Станок Электрика']));
    for(const station of stations.filter(asset=>asset.properties.craft_workbench)){
        assert.ok(C.findLocationAsset(station.baseType));
        assert.equal(station.visual.modelId,C.findLocationAsset(station.baseType).visual.modelId);
        assert.ok(station.dimensions.every(value=>value>0));
    }
    const aeration=C.findLocationAsset('station_aeration');
    assert.equal(aeration.visual.modelId,'aeration_column');
    assert.equal(aeration.properties.aeration_unit,true);
});
test('all new standalone objects have physical dimensions and distinct original models',async()=>{
    const {C}=await modules(),ids=C.LOCATION_ASSETS.map(a=>a.id);
    assert.equal(ids.length,new Set(ids).size);
    const assets=C.LOCATION_ASSETS.filter(a=>a.visual?.modelId&&a.id==='household_'+a.visual.modelId);
    assert.equal(assets.length,87);
    for(const a of assets){assert.ok(a.dimensions.every(n=>Number.isFinite(n)&&n>0));assert.equal(a.kind,'structure');}
    for(const id of ['fork','spoon','plate','glass','stones','broken_boards','litter'])assert.equal(C.findLocationAsset('household_'+id).passable,true);
    assert.equal(C.findLocationAsset('household_sandbag_wall').passable,false);
});
test('new purposes produce their own saved rooms and furniture rather than residential copies',async()=>{
    const expected={workshop:'workshop',cafe:'dining',clinic:'clinic',office:'office',school:'classroom',garage:'garage',bathhouse:'sauna'};
    for(const [buildingType,role]of Object.entries(expected)) {
        const {source,rooms,plan}=await fixture({shellOptions:{buildingType,width:16,depth:16,storeys:1,basements:0,wealth:'rich'}});
        assert.equal(plan.error,undefined,buildingType);
        assert.ok(rooms.length>0&&plan.objects.length>0,buildingType);
        assert.ok(source.filter(o=>o.properties.building_generation).every(o=>o.properties.building_generation.buildingType===buildingType));
        assert.ok(plan.objects.some(o=>o.properties.furniture_generation.role===role),buildingType);
        assert.ok(!plan.objects.some(o=>o.type.startsWith('household_bed_')),buildingType);
        if(buildingType==='cafe')assert.equal(rooms.filter(r=>r.name==='Кухня').length,1);
    }
});
test('new room assignments work explicitly without changing existing architecture',async()=>{
    const {F}=await modules(),{source,rooms}=await fixture({noLayout:true,shellOptions:{width:16,depth:16,storeys:1,basements:0}});
    for(const role of ['child','bath','toilet','dining','dressing','laundry','boiler','classroom','treatment','washroom','sauna']) {
        const plan=F.planBuildingFurniture(source,{buildingId:'b',scope:'room',roomId:rooms[0].id,level:0,role});
        assert.equal(plan.error,undefined,role);assert.ok(plan.objects.some(o=>o.properties.furniture_generation.role===role),role);
        assert.ok(plan.objects.every(o=>o.type!=='wall'&&o.type!=='floor'));
    }
});
test('new storage furniture keeps manually filled containers during regeneration',async()=>{
    const {F}=await modules(),{source,plan}=await fixture({shellOptions:{buildingType:'clinic',width:16,depth:16,storeys:1,basements:0,wealth:'rich'}});
    const additions=plan.objects.map((o,i)=>({...o,id:10000+i,revision:1}));
    const cabinet=additions.find(o=>o.type==='household_medical_cabinet');
    assert.ok(cabinet&&Array.isArray(cabinet.properties.contents));
    cabinet.properties.contents.push({item_id:1,quantity:1});
    assert.equal(F.isUnchangedFurniture(cabinet),false);
    const next=F.planBuildingFurniture([...source,...additions],{buildingId:'b',scope:'building',seed:'again'});
    assert.equal(next.error,undefined);assert.ok(!next.replacedIds.includes(cabinet.id));
});
