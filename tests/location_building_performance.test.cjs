const sceneContext = require('./helpers/locationSceneContext.cjs');
const createSceneContext = values => vm.createContext(sceneContext(values));
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {modules}=require('./location_furniture_fixture.cjs');
const scene=fs.readFileSync(path.join(__dirname,'../app/static/js/locationScene.js'),'utf8');
const code=name=>{const start=scene.indexOf(`function ${name}(`);assert.ok(start>=0);return scene.slice(start,scene.indexOf('\n}',start)+2)};

test('new building composes existing shell, rooms and furniture for complex shapes without changing the shell',async()=>{
    const {H,I,F,A}=await modules();
    const c=createSceneContext({...H,I,F,structureLevel:A.structureLevel,characterModels:new Map()});
    vm.runInContext('async '+code('planNewBuilding').replace("await import('./locationInteriorGenerator.js')",'I')
        .replace("await import('./locationFurnitureGenerator.js')",'F'),c);
    for(const shape of ['rectangle','L','T','U','C','cross','compound']){
        const options={minX:2,minZ:2,width:12,depth:12,storeys:2,basements:1,buildingId:'b',seed:'combined',shape,wealth:'rich',condition:'intact',groundHeight:()=>1};
        const shell=H.planBuildingShell(options),plan=await c.planNewBuilding({...options,contents:'furnished'});
        assert.equal(plan.error,undefined,shape);
        assert.equal(JSON.stringify(plan.objects.slice(0,shell.objects.length)),JSON.stringify(shell.objects));
        const furniture=plan.objects.filter(o=>o.properties.furniture_generation);
        assert.ok(furniture.length>0&&furniture.length<=256,shape);
        assert.deepEqual([...new Set(furniture.map(A.structureLevel))].sort((a,b)=>a-b),[-1,0,1]);
        assert.ok(plan.objects.some(o=>o.properties.layout_rooms?.length));
    }
});

test('furniture batches retain architecture and terrain, with idempotent socket echoes',async()=>{
    const {A}=await modules(),calls={};
    const floor={id:1,type:'floor',tile_x:4,tile_y:4,properties:{building_id:'b',level:0,dimensions:{width:6,depth:6,height:.12}}};
    const c=createSceneContext({...A,performance,currentLocationId:1,currentLocationData:{objects:[floor],grid_width:36,grid_height:36},editorSelections:[],
        isGroundItem:object=>object?.type==='ground_item',
        isObjectLightSource:o=>o?.type==='campfire',...Object.fromEntries(['addLocationObjectMesh','removeLocationObjectMesh','refreshArchitecturalMeshes','rebuildGrassDetailMesh','queueStaticLightFieldRefresh','applyFogOfWar','refreshLocationDarkness','refreshLocationFloorMenu','invalidateMovementMapCache','notifyEditorBuildingsChanged','refreshObjectLightSource'].map(name=>[name,()=>{calls[name]=(calls[name]||0)+1}]))});
    vm.runInContext(code('buildingGrassChangedTiles')+'\n'+code('applyLocationObjectsChange'),c);
    const batch={location_id:1,objects:[{id:2,type:'household_bed_single',properties:{building_id:'b',level:0}}]};
    c.applyLocationObjectsChange(batch);c.applyLocationObjectsChange(batch);
    assert.equal(c.currentLocationData.objects.length,2);assert.equal(calls.addLocationObjectMesh,1);
    assert.equal(calls.refreshArchitecturalMeshes,undefined);assert.equal(calls.rebuildGrassDetailMesh,undefined);
    assert.equal(calls.queueStaticLightFieldRefresh,undefined);assert.equal(calls.applyFogOfWar,1);
    c.applyLocationObjectsChange({location_id:1,deleted_ids:[2]});
    assert.equal(c.currentLocationData.objects[0],floor);assert.equal(calls.rebuildGrassDetailMesh,undefined);
    c.applyLocationObjectsChange({location_id:1,objects:[{id:3,type:'campfire',properties:{level:0}}]});
    assert.equal(calls.queueStaticLightFieldRefresh,1);assert.equal(calls.refreshObjectLightSource,1);
    c.applyLocationObjectsChange({location_id:1,objects:[{id:4,type:'campfire',properties:{level:0}}]});
    const before={...calls};
    c.applyLocationObjectsChange({location_id:1,deleted_ids:[3,4]});
    assert.equal(calls.refreshObjectLightSource-before.refreshObjectLightSource,2);
    assert.equal(calls.refreshLocationDarkness-before.refreshLocationDarkness,1,'batch deletion refreshes darkness once');
    assert.equal(calls.queueStaticLightFieldRefresh-before.queueStaticLightFieldRefresh,1);
    assert.deepEqual(c.currentLocationData.objects,[floor]);
});

test('grass coverage refresh includes old and new rotated slabs and ignores unchanged coverage',async()=>{
    const {A}=await modules();
    const c=createSceneContext({...A,currentLocationData:{grid_width:48,grid_height:48}});
    vm.runInContext(code('buildingGrassChangedTiles'),c);
    const old={id:1,type:'floor',tile_x:5,tile_y:5,properties:{building_id:'b',level:0,rotation:.4,dimensions:{width:6,depth:4,height:.12}}};
    const moved={...old,tile_x:17};
    const changed=c.buildingGrassChangedTiles([old],[moved],[old,moved]);
    assert.ok(changed.some(t=>t.x===5&&t.z===5));assert.ok(changed.some(t=>t.x===17&&t.z===5));
    assert.ok(changed.every(t=>t.x<24&&t.z<12));
    assert.equal(c.buildingGrassChangedTiles([old],[{...old,id:2}],[old,{...old,id:2}]).length,0);
    const wall={id:3,type:'wall',tile_x:5,tile_y:5,properties:{building_id:'other',level:0}};
    assert.equal(c.buildingGrassChangedTiles([old],[old,wall],[wall]).length,0);
});

test('largest shells cover the wealth and condition matrix, support copying, and bound furniture',async()=>{
    const {H,I,F,A}=await modules();
    for(const shape of ['rectangle','L','U','C','T','cross','compound'])for(const wealth of ['poor','ordinary','rich'])for(const condition of ['intact','worn','damaged','ruins']){
        const shell=H.planBuildingShell({minX:2,minZ:2,width:16,depth:16,storeys:4,basements:2,roofType:'gable',shape,wealth,condition,seed:'stress',buildingId:'b'});
        assert.equal(shell.error,undefined);
        const source=shell.objects;
        for(const level of [-2,-1,0,1,2,3]){
            const layout=I.planBuildingInterior(source,{buildingId:'b',level,seed:'stress'});
            assert.equal(layout.error,undefined);source.push(...layout.objects);
        }
        source.forEach((o,i)=>{o.id=i+1;o.revision=1});
        const before=JSON.stringify(source),furniture=F.planBuildingFurniture(source,{buildingId:'b',scope:'building',seed:'stress'});
        assert.equal(furniture.error,undefined);assert.ok(furniture.objects.length<=256);
        const copy=A.storeyPlan({buildingId:'b',sourceLevel:3,targetLevel:4,mode:'copy'},source);
        assert.equal(copy.error,undefined);assert.ok(copy.objects.every(o=>o.id===undefined&&o.properties.level===4));
        assert.equal(JSON.stringify(source),before);
        assert.ok(source.length+furniture.objects.length<5000);
        assert.ok(JSON.parse(JSON.stringify(copy)).objects.length>0);
    }
});
