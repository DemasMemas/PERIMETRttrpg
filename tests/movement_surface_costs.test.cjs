const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const src=fs.readFileSync(path.join(__dirname,'../app/static/js/movementSurfaceCosts.js'),'utf8');
const modulePromise=import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'));
const footprint=o=>[{x:o.tile_x,y:o.tile_y}],level=o=>o.properties?.level||0;
test('Однозначные terrain стоят два; wood/granite/mud/water не получают выдуманную классификацию',async()=>{
 const {buildMovementSurfaceCosts}=await modulePromise;
 for(const terrain of ['snow','sand','swamp','ice','grass','wood','granite','mud','water']){
  const costs=buildMovementSurfaceCosts({tiles_data:[[{terrain}]]},footprint,level);
  assert.equal(costs.get('0:0:0')||1,['snow','sand','swamp','ice'].includes(terrain)?2:1);
 }
});
test('Шесть существующих vegetation ID стоят два, пол перекрывает грунт, уровни независимы',async()=>{
 const {buildMovementSurfaceCosts}=await modulePromise;
 for(const species of ['bush','tall_grass'])for(const state of ['green','dry','dead']){
  const location={tiles_data:[[{terrain:'snow'}]],objects:[
   {type:'floor',tile_x:0,tile_y:0,properties:{level:0}},
   {type:'tree',tile_x:0,tile_y:0,properties:{level:1,visual_asset:'nature_'+species+'_'+state}}]};
  const costs=buildMovementSurfaceCosts(location,footprint,level);
  assert.equal(costs.has('0:0:0'),false);assert.equal(costs.get('0:0:1'),2);
 }
});
test('Клиентский A* выбирает доступный путь по цене, а не кратчайший через песок',async()=>{
 const {buildMovementSurfaceCosts}=await modulePromise;
 const tiles=Array.from({length:3},()=>Array.from({length:3},()=>({terrain:'grass'})));tiles[1][1].terrain='sand';
 const location={grid_width:3,grid_height:3,tiles_data:tiles};
 const costs=buildMovementSurfaceCosts(location,footprint,level);
 const scene=fs.readFileSync(path.join(__dirname,'../app/static/js/locationScene.js'),'utf8');
 const start=scene.indexOf('function findMovementPath('),end=scene.indexOf('function clearStructureMovePreview()',start);
 const context=vm.createContext({currentLocationData:location,hasStoreyNavigation:()=>false,getGrappleMovementContext:()=>null,
  ordinaryHeightTransitionAllowed:(a,b)=>Math.abs(b-a)<1,getTileHeight:()=>1,
  getTileMovementProfile:(x,y)=>({blocked:false,climbCost:0,travelCost:costs.get(x+':'+y+':0')||1})});
 vm.runInContext(scene.slice(start,end),context);
 const route=context.findMovementPath(0,1,2,1);assert.equal(route.cost,2);
 assert.equal(route.path.some(([x,y])=>x===1&&y===1),false);
});

test('Предпросмотр сохраняет существующий иммунитет Полёта к сложным поверхностям',()=>{
 const source=fs.readFileSync(path.join(__dirname,'../app/static/js/locationScene.js'),'utf8');
 const first=source.indexOf('function resolveMovementType('),last=source.indexOf('function getMovementModeAvailability(',first);
 const c=vm.createContext({combatState:{current_character:{posture:'standing',ignores_difficult_surface:true}},
 COMBAT_MOVEMENT_TYPES:{run:{divisor:2}},COMBAT_POSTURES:{standing:{movementMultiplier:1}}});
 vm.runInContext(source.slice(first,last),c);
 assert.equal(c.getMovementModeRouteCost({cost:10,distance:2,climbCost:0},'run').movementPoints,1);
});

test('Клиентская граница обычного шага:0.99 допускается,1/1.99/2 требуют отдельного действия',async()=>{
 const {ordinaryHeightTransitionAllowed}=await modulePromise;
 for(const [rise,allowed] of [[.99,true],[1,false],[1.99,false],[2,false],[-1,false],[-2,false]]){
  assert.equal(ordinaryHeightTransitionAllowed(4,4+rise),allowed);
 }
 assert.equal(ordinaryHeightTransitionAllowed(1,NaN),false);
});
