const sceneContext = require('./helpers/locationSceneContext.cjs');
const createSceneContext = values => vm.createContext(sceneContext(values));
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const read=name=>fs.readFileSync(path.join(__dirname,'../app/static/js',name),'utf8');
const load=()=>import(`data:text/javascript;base64,${Buffer.from(read('locationArchitecture.js')).toString('base64')}`);
const part=(name,next)=>{const s=read('locationScene.js'),a=s.indexOf(`function ${name}(`),b=s.indexOf(`function ${next}(`,a+1);return s.slice(s.slice(a-6,a)==='async '?a-6:a,b).replaceAll('export ','').replace(/async\s*$/,'');};
const make=(type,x,z,w,d,level=0,extra={})=>({id:`${type}-${x}-${z}`,type,name:type,tile_x:Math.floor(x),tile_y:Math.floor(z),properties:{building_id:'A',level,
    offset_x:x-Math.floor(x)-.5,offset_z:z-Math.floor(z)-.5,dimensions:{width:w,depth:d,height:type==='floor'?.12:type==='window'?1:type==='door'?2:2.4},...extra}});
const building=()=>[make('floor',5,5,8,8),make('wall',5,1,8,.2),make('wall',5,9,8,.2),
    make('wall',1,5,8,.2,0,{rotation:Math.PI/2}),make('wall',9,5,8,.2,0,{rotation:Math.PI/2}),
    make('wall',5,5,8,.12),make('door',5,1,.9,.18),make('window',1,4,1.2,.14,0,{rotation:Math.PI/2,elevation:1}),
    make('household_wardrobe',3,3,1,.5,0,{contents:[{id:'real-inventory-id'}]})];

test('signed levels retain legacy zero and copy any storey independently',async()=>{
    const {structureLevel,structureLevelOffset,storeyPlan}=await load();
    for(const level of [-3,-2,-1,0,1,4]){
        assert.equal(structureLevel({properties:{level}}),level);
        assert.equal(structureLevelOffset({properties:{level}}),level*2.4);
    }
    assert.equal(structureLevel({}),0);assert.equal(structureLevel({properties:{level:1.5}}),0);
    const source=building(),snapshot=JSON.stringify(source);
    const plan=storeyPlan({buildingId:'A',sourceLevel:0,targetLevel:-2,mode:'copy'},source);
    assert.equal(plan.objects.length,source.length);assert.equal(JSON.stringify(source),snapshot);
    assert.ok(plan.objects.every(o=>o.properties.level===-2&&o.id==null));
    assert.deepEqual(plan.objects.at(-1).properties.contents,[]);
    plan.objects[0].properties.dimensions.width=3;
    assert.equal(source[0].properties.dimensions.width,8);
    assert.ok(storeyPlan({buildingId:'A',sourceLevel:0,targetLevel:0},source).error);
});

test('empty and new-layout modes retain the external contour and its openings only',async()=>{
    const {storeyPlan}=await load(),source=building();
    const options={buildingId:'A',sourceLevel:0,targetLevel:1};
    const empty=storeyPlan({...options,mode:'empty'},source).objects;
    assert.equal(empty.filter(o=>o.type==='wall').length,4);
    assert.equal(empty.filter(o=>['door','window'].includes(o.type)).length,2);
    assert.ok(!empty.some(o=>o.type==='household_wardrobe'));
    const fresh=storeyPlan({...options,mode:'layout'},source).objects;
    assert.equal(fresh.filter(o=>o.type==='wall').length,5);
    assert.equal(fresh.filter(o=>o.type==='door').length,2);
    assert.equal(source.filter(o=>o.type==='wall').length,5);
});

test('orthogonal composed floors survive copying and stairwell removes upper slab only',async()=>{
    const {storeyPlan,floorSlabParts}=await load();
    const floors=[make('floor',3,3,2,6),make('floor',6,5,4,2)];
    const copied=storeyPlan({buildingId:'A',sourceLevel:0,targetLevel:-1,mode:'empty'},floors).objects;
    assert.equal(copied.length,2);
    const basement=make('floor',5,5,8,8,-1);
    const ground=make('floor',5,5,8,8,0);
    const stair=make('stairs',5,5,1.2,3,-1);
    const parts=floorSlabParts(ground,[basement,ground,stair]);
    assert.ok(Math.abs(parts.reduce((sum,p)=>sum+p.width*p.depth,0)-(64-3.6))<1e-8);
    assert.equal(floorSlabParts(basement,[basement,ground,stair]).length,1);
});

test('partial and rotated basement contours leave surrounding ground and gaps intact',async()=>{
    const {structurePose}=await load();
    const {cutawayContains}=await import(`data:text/javascript;base64,${Buffer.from(read('locationBasementCutaway.js')).toString('base64')}`);
    const source=[make('floor',8,5,4,6,-1),make('floor',3,3,2,2,-1,{rotation:Math.PI/4})];
    const snapshot=JSON.stringify(source),footprints=source.map(structurePose);
    assert.equal(cutawayContains(footprints,8,5),true);
    assert.equal(cutawayContains(footprints,5,5),false); // unexcavated half of the house
    assert.equal(cutawayContains(footprints,11,5),false);
    assert.equal(cutawayContains(footprints,3,3),true);
    assert.equal(cutawayContains(footprints,4.2,4.2),false); // rotated rectangle, not its AABB
    assert.equal(cutawayContains(footprints,5,3),false); // gap between disconnected floors
    assert.equal(JSON.stringify(source),snapshot);
});

test('terrain cutaway composes with lighting rebuilds and uses no extra texture sampler',async()=>{
    const {structurePose}=await load();
    const {createBasementCutaway}=await import(`data:text/javascript;base64,${Buffer.from(read('locationBasementCutaway.js')).toString('base64')}`);
    class Vector { set(...values){this.values=values;return this;} }
    const cut=createBasementCutaway({Vector2:Vector,Vector4:Vector});
    const floor=make('floor',8,5,4,6,-1),snapshot=JSON.stringify(floor);
    let priorCalls=0;
    const material={onBeforeCompile(){priorCalls++;},customProgramCacheKey:()=> 'existing-terrain',needsUpdate:false};
    const root={traverse:visit=>visit({material})};
    const shader=()=>({uniforms:{},vertexShader:'#include <common>\n#include <project_vertex>',
        fragmentShader:'#include <common>\n#include <clipping_planes_fragment>'});
    cut.update([structurePose(floor)]);cut.attach(root);
    const first=material.onBeforeCompile;
    material.onBeforeCompile=s=>first(s); // existing lighting wraps the cutaway
    cut.attach(root);
    let compiled=shader();material.onBeforeCompile(compiled);
    assert.equal(priorCalls,1);
    assert.equal(compiled.vertexShader.match(/varying vec2 vBasementWorldXZ;/g).length,1);
    assert.ok(!compiled.fragmentShader.includes('sampler2D'));
    assert.equal(compiled.uniforms.basementRectCount.value,1);
    material.onBeforeCompile=()=>{priorCalls++;}; // lighting restores its original callback
    cut.attach(root);compiled=shader();material.onBeforeCompile(compiled);
    assert.equal(compiled.uniforms.basementRectCount.value,1);
    cut.update([]);assert.equal(compiled.uniforms.basementRectCount.value,0);
    assert.equal(JSON.stringify(floor),snapshot);cut.dispose();
});

test('floor menu uses the actual signed levels with zero as ground',()=>{
    const select={options:[],replaceChildren(...options){this.options=options;}};
    const down={},up={};
    const menu={querySelector:s=>s==='select'?select:s==='[data-floor-down]'?down:up};
    const container={},dock={appendChild(m){m.parentNode=this;}},actions={};
    menu.parentNode=container;
    const context=createSceneContext({locationViewLevel:-1,locationFloorCount:()=>3,locationMinimumLevel:()=>-3,
        window:{locationEditMode:true,locationEditorTool:'building'},
        refreshLocationFloorDeleteAction(){},
        document:{getElementById:id=>id==='location-floor-menu'?menu:id==='location-building-actions'?actions:dock},Option:class{constructor(text,value){this.text=text;this.value=value;}}});
    vm.runInContext(part('refreshLocationFloorMenu','updateLocationLampFixtureFloors'),context);
    context.refreshLocationFloorMenu();
    assert.deepEqual(Array.from(select.options,o=>o.value),['-3','-2','-1','0','1','2']);
    assert.equal(select.value,'-1');assert.equal(down.disabled,false);
    assert.equal(menu.parentNode,container,'floor navigation stays in its game position while editing');
    assert.equal(actions.parentNode,dock);assert.equal(actions.hidden,false);
    context.window.locationEditMode=false;context.refreshLocationFloorMenu();
    assert.equal(menu.parentNode,container);assert.equal(actions.hidden,true);
});

test('stair movement tolerates a RAF timestamp before the event and ends at server height',()=>{
    const vector=(x=0,y=0,z=0)=>({x,y,z,set(x,y,z){Object.assign(this,{x,y,z});},copy(v){this.set(v.x,v.y,v.z);},clone(){return vector(this.x,this.y,this.z);}});
    const callbacks=[],entry={floorLevel:0,model:{position:vector(1.5,1,1.5),traverse(){}},label:{position:vector()}};
    const context=createSceneContext({getCharacterModelEntry:()=>entry,STOREY_HEIGHT:2.4,currentLocationData:{objects:[]},
        queueTrueSightRefresh(){},
        getTileHeight:()=>1,attachStaticLightFieldToRoot(){},locationStaticLightField:null,
        tintLocationMesh(){},darknessFactorAt:()=>1,characterLabelOffset:()=>1.9,
        invalidateMovementMapCache(){},applyFogOfWar(){},performance:{now:()=>100},document:{hidden:false},
        requestAnimationFrame:callback=>callbacks.push(callback)});
    vm.runInContext(part('updateCharacterPosition','removeCharacterFromLocation'),context);
    context.updateCharacterPosition(1,3,1,{floor_level:1,height:3.435,path:[
        {x:1,y:1,level:0,height:1},{x:2,y:1,level:0,height:2.4},{x:3,y:1,level:1,height:3.435}]});
    callbacks.shift()(99);assert.equal(entry.model.position.y,1);
    callbacks.shift()(280);assert.equal(entry.model.position.y,2.4);
    callbacks.shift()(460);assert.equal(entry.model.position.y,3.435);assert.equal(entry.floorLevel,1);
});

test('an old manual covering panel cannot protrude through a newly added upper slab',async()=>{
    const architecture=await load(),roof=make('roof',5,5,2,2,0,{elevation:2.4});
    const floor=make('floor',5,5,8,8,1),child={isMesh:true,material:{}};
    const mesh={userData:{locationObject:roof},position:{y:3.4},traverse:callback=>callback(child)};
    const context=createSceneContext({...architecture,currentLocationData:{objects:[floor,roof]},
        locationObjectMeshes:[mesh],roofMeshes:[],camera:{position:{distanceTo:()=>20}},controls:{target:{}},
        locationViewLevel:1,locationRoofsVisible:true,editMode:false,getTileHeight:()=>1,FLOOR_SURFACE_OFFSET:.035,
        updateStoreyTerrainVisibility(){},getLocationBuildingViews:()=>new Map(),locationBuildingViewsDirty:false,
        roofOpacityForCamera:()=>1});
    vm.runInContext(part('updateRoofVisibility','rebuildLocationRoofs'),context);
    context.updateRoofVisibility();assert.equal(child.visible,false);
    context.currentLocationData.objects=[roof];context.updateRoofVisibility();assert.equal(child.visible,true);
    assert.equal(roof.properties.level,0);
});


test('ladder and hatch cut a shared upper opening and keep the lower floor intact',async()=>{
    const {floorSlabParts}=await load();
    const floor=make('floor',5,5,8,8,1),bottom=make('floor',5,5,8,8,0);
    const ladder=make('ladder',5,5,.9,.9,0,{dimensions:{width:.9,depth:.9,height:2.4}});
    const hatch=make('hatch',5,5,1,1,1,{dimensions:{width:1,depth:1,height:.08},is_open:false});
    const area=parts=>parts.reduce((sum,p)=>sum+p.width*p.depth,0);
    const objects=[bottom,floor,ladder,hatch];
    assert.ok(Math.abs(area(floorSlabParts(floor,objects))-63)<1e-8);
    assert.equal(area(floorSlabParts(bottom,objects)),64);
    hatch.properties.is_open=true;
    assert.ok(Math.abs(area(floorSlabParts(floor,objects))-63)<1e-8);
    const other={...hatch,properties:{...hatch.properties,building_id:'elsewhere'}};
    assert.ok(Math.abs(area(floorSlabParts(floor,[bottom,floor,other]))-64)<1e-8);
});


test('drop is a distinct action for old hatches and lidless openings, never an upward route',async()=>{
    const architecture=await load(),actor={floorLevel:2,model:{userData:{characterId:7}}};
    const opening=make('floor_opening',5,5,1,1,2),closed=make('hatch',7,5,1,1,2,{is_open:false,interactions:['toggle_door']});
    const emitted=[];
    const context=createSceneContext({...architecture,currentLocationData:{objects:[opening,closed]},currentLocationId:9,
        getCurrentInteractionActor:()=>actor,getCharacterTileDistance:()=>1,getActionRequirement:()=>({max_distance:1}),
        localStorage:{getItem:()=> 'test-token'},window:{socket:{emit:(name,payload)=>emitted.push({name,payload})}},
        fetch:async()=>({ok:true,json:async()=>({route:{landing:{x:5.1,y:5.2},floor_level:0}})}),
        hideHighlight(){},hideStructureInteraction(){},pendingStructureAction:null,
        objectInteractions:{},isFireObject:()=>false,findLocationAsset:()=>null});
    vm.runInContext(part('isStructureActionAllowed','updateInteractiveObject').replace(/async\s*$/,''),context);
    vm.runInContext(part('useLocationVerticalAccess','getObjectActions'),context);
    vm.runInContext(part('getObjectActions','getStructureActions'),context);
    assert.equal(context.isStructureActionAllowed(opening,'drop_down',7),true);
    assert.equal(context.isStructureActionAllowed(opening,'level_up',7),false);
    assert.equal(context.isStructureActionAllowed(closed,'drop_down',7),false);
    assert.ok(context.getObjectActions(closed).includes('drop_down')); // old saves gain the action too
    assert.equal(await context.useLocationVerticalAccess(opening.id,'up',7),false);
    assert.equal(emitted.length,0);
    assert.equal(await context.useLocationVerticalAccess(opening.id,'drop',7),true);
    assert.equal(emitted[0].payload.special_action,'drop_down');
    assert.equal(emitted[0].payload.object_id,opening.id);
    assert.equal(emitted[0].payload.floor_level,0); // Сервер подтвердил этаж и дробную точку приземления.
    assert.equal(emitted[0].payload.x,5.1);
    assert.equal(emitted[0].payload.y,5.2);
    actor.floorLevel=1;
    assert.equal(context.isStructureActionAllowed(opening,'drop_down',7),false);
    const source=[make('floor',5,5,8,8,2),opening];
    const area=architecture.floorSlabParts(source[0],source).reduce((sum,p)=>sum+p.width*p.depth,0);
    assert.equal(area,63);
});
