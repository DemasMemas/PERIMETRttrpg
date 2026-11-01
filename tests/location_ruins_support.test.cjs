const {test}=require('node:test'),assert=require('node:assert/strict');
const {modules,fixture}=require('./location_furniture_fixture.cjs');
test('interaction picking skips invisible roof hits and selects the visible object below',()=>{
    const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
    const source=fs.readFileSync(path.join(__dirname,'../app/static/js/locationScene.js'),'utf8');
    const start=source.indexOf('function getLocationObjectAtScreen('),end=source.indexOf('\nexport async function useLocationVerticalAccess',start);
    const roof={visible:true,userData:{locationObjectId:1}},floor={visible:true,userData:{locationObjectId:2}};
    const hits=[{object:{visible:false,userData:{},parent:roof}},{object:{visible:true,userData:{},parent:floor}}];
    const ctx=vm.createContext({renderer:{domElement:{getBoundingClientRect:()=>({left:0,top:0,width:100,height:100})}},
        camera:{},currentLocationId:24,locationObjectMeshes:[roof,floor],locationObjectPickCache:{key:null},mouse:{},
        raycaster:{setFromCamera:()=>{}},tileCubes:[],profiledLocationRaycast:(_,__,kind)=>kind==='structure'?hits:[],
        visibleEditorHit:hit=>hit.object.visible,locationHitOccludedByTerrain:()=>false});
    vm.runInContext(source.slice(start,end),ctx);
    assert.equal(vm.runInContext('getLocationObjectAtScreen(50,50)',ctx),floor);
});
function object(type,x,z,width,depth,height,extra={}){return{type,tile_x:Math.floor(x),tile_y:Math.floor(z),properties:{building_id:'b',level:0,
    dimensions:{width,depth,height},offset_x:x-Math.floor(x)-.5,offset_z:z-Math.floor(z)-.5,...extra}};}
test('mounted furniture needs a full-height surviving wall along its entire width',async()=>{
    const {F}=await modules(),wall=object('wall',5,5,4,.2,2.4),board=object('household_blackboard',5,5.152,1.8,.08,.9,{elevation:1.035});
    assert.equal(F.hasFurnitureSupport([wall],board),true);
    assert.equal(F.hasFurnitureSupport([{...wall,properties:{...wall.properties,dimensions:{width:4,depth:.2,height:.7}}}],board),false);
    assert.equal(F.hasFurnitureSupport([object('wall',4,5,.8,.2,2.4),object('wall',6,5,.8,.2,2.4)],board),false);
    assert.equal(F.hasFurnitureSupport([],board),false);
});
test('ceiling bulbs need overhead geometry, including real roof holes and stair openings',async()=>{
    const {F}=await modules(),bulb=object('ceiling_bulb',5,5,.32,.32,.7,{elevation:2.36});
    const roof=object('roof',5,5,4,4,.1,{elevation:2.4});
    assert.equal(F.hasFurnitureSupport([],bulb),false);
    assert.equal(F.hasFurnitureSupport([roof],bulb),true);
    const missing={...roof,properties:{...roof.properties,roof_geometry:[-2,0,-2,-1,0,-2,-2,0,-1]}};
    assert.equal(F.hasFurnitureSupport([missing],bulb),false);
    const floor=object('floor',5,5,4,4,.12,{level:1});
    assert.equal(F.hasFurnitureSupport([floor],bulb),true);
    assert.equal(F.hasFurnitureSupport([floor,object('floor_opening',5,5,2,2,.1,{level:1})],bulb),false);
});
test('furniture generated in damaged buildings always has physical mounting support',async()=>{
    const {F}=await modules();
    for(const condition of ['intact','damaged','ruins'])for(const buildingType of ['residential','school','clinic','office']){
        const {source,plan}=await fixture({shellOptions:{condition,buildingType,width:16,depth:16}});
        for(const o of plan.objects)assert.ok(F.hasFurnitureSupport(source,o),`${condition}/${buildingType}/${o.type}`);
    }
});
test('new outdoor variants are available for ordinary placement',async()=>{
    const {C}=await modules();
    for(const species of ['oak','birch','pine','bush','tall_grass'])for(const state of ['green','dry','dead']){
        const a=C.findLocationAsset(`nature_${species}_${state}`);assert.ok(a);assert.ok(a.dimensions.every(n=>n>0));
    }
    for(const id of ['monument','monument_ruined'])assert.ok(C.findLocationAsset('household_'+id));
});
test('new partitions never end inside an exterior window opening',async()=>{
    const {I,H,A}=await modules();
    for(const seed of ['0','1','2','3','pripyat-2-2026']){
        const shell=H.planBuildingShell({minX:2,minZ:2,width:16,depth:12,storeys:1,buildingId:'b',seed}).objects;
        const plan=I.planBuildingInterior(shell,{buildingId:'b',seed});
        for(const o of plan.objects.filter(o=>o.type==='wall')){
            const p=A.structurePose(o);
            for(const w of shell.filter(o=>o.type==='window').map(A.structurePose)){
                for(const t of [-.5,.5]){
                    const dx=p.x+p.axis.x*p.width*t-w.x,dz=p.z+p.axis.z*p.width*t-w.z;
                    assert.ok(Math.abs(dx*w.axis.x+dz*w.axis.z)>w.width/2+.05||Math.abs(dx*w.normal.x+dz*w.normal.z)>.3);
                }
            }
        }
    }
});
test('sanitary rooms do not connect multiple destinations or contain a staircase',async()=>{
    const {I,H,A}=await modules();let checked=0;
    for(const condition of ['intact','damaged','ruins'])for(let seed=0;seed<8;seed++){
        const source=H.planBuildingShell({minX:2,minZ:2,width:16,depth:16,storeys:1,buildingId:'b',seed:String(seed),condition}).objects;
        const plan=I.planBuildingInterior(source,{buildingId:'b',seed:String(seed),condition});
        for(const room of plan.rooms.filter(r=>/^(Санузел|Туалет|Ванная)$/.test(r.name))){
            checked++;
            const destinations=new Set();
            for(const door of plan.connections){
                const near=plan.rooms.filter(r=>r.cells.some(c=>Math.hypot(c.x+.5-door.x,c.z+.5-door.z)<1));
                if(near.includes(room))for(const r of near)if(r!==room)destinations.add(r.id);
            }
            assert.ok(destinations.size<=1);
            for(const stair of source.filter(o=>o.type==='stairs').map(A.structurePose))assert.ok(!room.cells.some(c=>Math.hypot(c.x+.5-stair.x,c.z+.5-stair.z)<1));
        }
    }
    assert.ok(checked>0);
});
