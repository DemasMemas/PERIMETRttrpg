const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const read=name=>fs.readFileSync(path.join(__dirname,'../app/static/js',name),'utf8');
const url=source=>`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const load=name=>import(url(read(name)));

test('stairs have continuous treads, bottom pivot and exact editable extents',async()=>{
    const {stairStepPlan}=await load('locationArchitecture.js');
    for(const size of [{width:1.2,depth:3.6,height:2.4},{width:.8,depth:1.5,height:.75}]){
        const steps=stairStepPlan(size);
        assert.equal(steps.at(-1).height,size.height);
        assert.ok(steps.every(s=>s.width===size.width&&s.height>0&&s.height<=size.height));
        assert.ok(Math.abs(steps[0].z-steps[0].depth/2+size.depth/2)<1e-10);
        assert.ok(Math.abs(steps.at(-1).z+steps.at(-1).depth/2-size.depth/2)<1e-10);
        for(let i=1;i<steps.length;i++){
            assert.ok(Math.abs(steps[i].z-steps[i-1].z-steps[i].depth)<1e-10);
            assert.ok(steps[i].height-steps[i-1].height<=.200001);
        }
    }
});

test('manual covering panels follow roof cutaway and stay pickable while editing',async()=>{
    const architecture=await load('locationArchitecture.js');
    const roof=await import(url(read('locationRoof.js').replace("'./locationArchitecture.js'",`'${url(read('locationArchitecture.js'))}'`)));
    const scene=read('locationScene.js'),start=scene.indexOf('function updateRoofVisibility(');
    const code=scene.slice(start,scene.indexOf('\n}',start)+2);
    for(const type of ['roof','ceiling']){
        const child={isMesh:true,material:{}};
        const object={type,tile_x:4,tile_y:4,properties:{dimensions:{width:2,depth:2,height:.12},elevation:2.4}};
        const mesh={position:{y:3.4},userData:{locationObject:object},traverse:fn=>fn(child)};
        const context=vm.createContext({firstPersonView:null,...architecture,...roof,currentLocationData:{objects:[]},camera:{position:{x:4.5,y:4,z:4.5,distanceTo:()=>4}},
            controls:{target:{x:4.5,y:1,z:4.5}},locationObjectMeshes:[mesh],roofMeshes:[],locationObjectPickCache:{key:null},
            updateStoreyTerrainVisibility() {}, locationViewLevel:0, editMode:false,locationRoofsVisible:true,locationBuildingViewsDirty:false,getLocationBuildingViews:()=>new Map()});
        vm.runInContext(code,context);context.updateRoofVisibility();
        assert.equal(child.visible,false);
        context.camera.position.y=25;context.updateRoofVisibility();
        assert.equal(child.visible,true);assert.equal(child.material.opacity,1);
        context.locationRoofsVisible=false;context.updateRoofVisibility();assert.equal(child.visible,false);
        context.editMode=true;context.updateRoofVisibility();assert.equal(child.visible,false);
        context.locationRoofsVisible=true;context.updateRoofVisibility();assert.equal(child.visible,true);
        assert.equal(child.material.opacity,1);
    }
});

test('construction library reuses wall and door types, and disabled automatic roofs survive JSON',async()=>{
    const {LOCATION_ASSETS}=await load('locationAssetCatalog.js');
    assert.equal(LOCATION_ASSETS.find(a=>a.id==='inner_wall').baseType,'wall');
    assert.equal(LOCATION_ASSETS.find(a=>a.id==='opening').baseType,'door');
    for(const id of ['floor','wall','door','window','roof','ceiling','stairs'])assert.ok(LOCATION_ASSETS.some(a=>a.id===id&&a.kind==='structure'));
    const roof=await import(url(read('locationRoof.js').replace("'./locationArchitecture.js'",`'${url(read('locationArchitecture.js'))}'`)));
    const floor={id:1,type:'floor',tile_x:4,tile_y:4,properties:{roof_enabled:false,dimensions:{width:2,depth:2,height:.12}}};
    assert.deepEqual(roof.roofPlans(JSON.parse(JSON.stringify([floor]))),[]);
});
