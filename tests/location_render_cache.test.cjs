const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const read=n=>fs.readFileSync(path.join(__dirname,'../app/static/js',n),'utf8');
const load=()=>import('data:text/javascript;base64,'+Buffer.from(read('locationShadowCache.js')).toString('base64'));
test('camera cutaways retain physical casters during shadow rendering and restore display state afterwards',async()=>{
    const {createLocationShadowPass}=await load();
    const material={opacity:0,side:0},mesh={visible:false,castShadow:false,material,userData:{physicalShadow:{visible:true,castShadow:true,opacity:1,side:2}}};
    const root={visible:false,userData:{physicalShadowVisible:true,physicalShadowMeshes:[mesh]}};
    let rendered=0;const renderer={shadowMap:{render(){assert.equal(root.visible,true);assert.equal(mesh.visible,true);assert.equal(mesh.castShadow,true);assert.equal(material.opacity,1);assert.equal(material.side,2);rendered++;}}};
    const original=renderer.shadowMap.render,pass=createLocationShadowPass(renderer,()=>[root]);
    renderer.shadowMap.render();assert.equal(rendered,1);assert.equal(root.visible,false);assert.equal(mesh.visible,false);assert.equal(mesh.castShadow,false);assert.equal(material.opacity,0);
    assert.throws(()=>pass.run(()=>{throw Error('test');}));assert.equal(root.visible,false);
    root.userData.physicalShadowVisible=false;pass.run(()=>assert.equal(root.visible,false));
    pass.dispose();assert.equal(renderer.shadowMap.render,original);
});
function node(extra={}){return{position:{x:0,y:0,z:0},quaternion:{x:0,y:0,z:0,w:1},scale:{x:1,y:1,z:1},visible:true,layers:{mask:1},...extra};}
test('Скрытие этажа камерой сохраняет физическую тень досок вместе с перекрытием',async()=>{
    const {createLocationShadowPass}=await load(),board={visible:false,castShadow:true,userData:{physicalShadow:{visible:true,castShadow:true}}};
    const root={visible:false,userData:{physicalShadowVisible:true,floorAttachedMeshes:[board],physicalShadowMeshes:[board]}};
    const renderer={shadowMap:{render(){assert.equal(root.visible,true);assert.equal(board.visible,true);}}};
    const pass=createLocationShadowPass(renderer,()=>[root]);
    renderer.shadowMap.render();assert.equal(root.visible,false);assert.equal(board.visible,false);pass.dispose();
});
async function setup(){
    const {createLocationShadowCache}=await load();
    const parent=node(),material={version:0,visible:true,opacity:1,alphaTest:0};
    const mesh=node({parent,isMesh:true,castShadow:true,geometry:{id:1,attributes:{position:{version:0},normal:{version:0}},index:{version:0},drawRange:{start:0,count:30}},material});
    const light=node({target:node(),castShadow:true,shadow:{autoUpdate:true,needsUpdate:false,mapSize:{x:2048,y:2048},camera:{left:-10,right:10,top:10,bottom:-10,near:.1,far:100}}});
    const entries=[mesh],scene={traverseVisible(fn){for(const m of entries)if(m.visible&&m.parent.visible)fn(m);}},renderer={shadowMap:{enabled:true,needsUpdate:false}};
    const cache=createLocationShadowCache(light),update=()=>{light.shadow.needsUpdate=false;cache.update(scene,renderer);return light.shadow.needsUpdate;};
    return{mesh,parent,material,light,entries,scene,renderer,cache,update};
}
test('static sunlight shadows survive camera-only frames and restore their update policy on disposal',async()=>{
    const s=await setup();assert.equal(s.light.shadow.autoUpdate,false);
    assert.equal(s.update(),true);assert.equal(s.update(),false);assert.equal(s.update(),false);
    s.cache.dispose();assert.equal(s.light.shadow.autoUpdate,true);
});
test('moving a caster or its parent, editing geometry and changing opacity update its shadow immediately',async()=>{
    const s=await setup();s.update();
    for(const mutate of [()=>s.mesh.position.x++,()=>s.parent.quaternion.y=.1,()=>s.mesh.geometry.attributes.position.version++,
        ()=>s.mesh.geometry.attributes.normal.version++,()=>s.mesh.geometry.index.version++,()=>s.material.opacity=.5,
        ()=>s.light.position.x++,()=>s.light.target.position.z++,()=>s.light.shadow.camera.right++,()=>s.renderer.shadowMap.needsUpdate=true]){
        mutate();assert.equal(s.update(),true);s.renderer.shadowMap.needsUpdate=false;assert.equal(s.update(),false);
    }
});
test('hidden, removed, replaced and instanced casters invalidate sunlight shadows',async()=>{
    const s=await setup();s.update();s.parent.visible=false;assert.equal(s.update(),true);assert.equal(s.update(),false);
    s.parent.visible=true;assert.equal(s.update(),true);assert.equal(s.update(),false);
    s.entries[0]=node({...s.mesh,geometry:{...s.mesh.geometry,id:2}});assert.equal(s.update(),true);assert.equal(s.update(),false);
    s.entries[0].instanceMatrix={version:1};assert.equal(s.update(),true);s.entries[0].instanceMatrix.version++;assert.equal(s.update(),true);
    s.entries.length=0;assert.equal(s.update(),true);assert.equal(s.update(),false);
});
test('animated skin, morphs and custom depth materials retain per-frame shadow updates',async()=>{
    for(const props of [{isSkinnedMesh:true},{morphTargetInfluences:[0]},{customDepthMaterial:{}}]){
        const s=await setup();Object.assign(s.mesh,props);assert.equal(s.update(),true);assert.equal(s.update(),true);
    }
});
test('Общий материал проверяется один раз за кадр, а его изменение обновляет тень',async()=>{
    const s=await setup();let reads=0,version=0;
    Object.defineProperty(s.material,'version',{get(){reads++;return version;}});
    s.entries.push(node({...s.mesh,parent:s.parent,material:s.material}));
    assert.equal(s.update(),true);assert.equal(reads,1);
    assert.equal(s.update(),false);assert.equal(reads,2);
    version++;assert.equal(s.update(),true);assert.equal(reads,3);
    assert.equal(s.update(),false);
});
test('camera movement does not rebuild terrain clipping and geometry, while storey and object changes do',()=>{
    const source=read('locationScene.js'),start=source.indexOf('function updateRoofVisibility(');
    const fn=source.slice(start,source.indexOf('\n}',start)+2);
    let clipping=0,reads=0,views=new Map();
    const mesh={position:{y:3.4},userData:{locationObject:{type:'roof',properties:{}}},traverse:cb=>cb({isMesh:true,material:{}})};
    const ctx=vm.createContext({camera:{position:{x:0,y:25,z:0,distanceTo:()=>25}},controls:{target:{x:0,y:0,z:0}},
        currentLocationData:{objects:[],tiles_data:[]},locationBuildingViewsDirty:false,getLocationBuildingViews:()=>views,
        updateStoreyTerrainVisibility:()=>clipping++,locationViewLevel:0,editMode:false,locationRoofsVisible:true,
        locationObjectMeshes:[mesh],roofMeshes:[],structurePose:()=>{reads++;return{height:.1};},
        structureLevel:()=>0,roofOpacityForCamera:()=>1,locationObjectPickCache:{},applyFogOfWar(){},updateLocationLampFixtureFloors(){}});
    vm.runInContext(fn,ctx);ctx.updateRoofVisibility();assert.equal(clipping,1);assert.equal(reads,1);
    ctx.updateRoofVisibility();assert.equal(clipping,1);assert.equal(reads,1);
    ctx.camera.position.x++;ctx.updateRoofVisibility();assert.equal(clipping,1);assert.equal(reads,1);
    ctx.locationViewLevel=1;ctx.updateRoofVisibility();assert.equal(clipping,2);assert.equal(reads,1);
    views=new Map();ctx.updateRoofVisibility();assert.equal(clipping,3);assert.equal(reads,2);
    ctx.updateRoofVisibility.lastState=null;ctx.updateRoofVisibility();assert.equal(clipping,4);assert.equal(reads,3);
});
