const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'..'),fixturePath=path.join(__dirname,'fixtures/terrain_los_parity.json');
const terrainSource=fs.readFileSync(path.join(root,'app/static/js/locationTerrain.js'),'utf8');
const terrainUrl='data:text/javascript;base64,'+Buffer.from(terrainSource).toString('base64');
const lineSource=fs.readFileSync(path.join(root,'app/static/js/terrainLineOfSight.js'),'utf8').replace("'./locationTerrain.js'",JSON.stringify(terrainUrl));
const modules=Promise.all([import(terrainUrl),import('data:text/javascript;base64,'+Buffer.from(lineSource).toString('base64'))]);
function cases(){
 const make=(name,fn)=>({name,tiles:Array.from({length:5},(_,z)=>Array.from({length:5},(_,x)=>fn(x,z)))});
 const a=[make('flat',()=>({terrain:'grass',height:1})),
 make('hill',(x)=>({terrain:'grass',height:x===2?6:1})),
 make('sand',(x)=>({terrain:'sand',height:x===2?6:1})),
 make('diagonal',(x,z)=>({terrain:'grass',height:x===z?5:1})),
 make('slope',(x,z)=>({terrain:'grass',height:1+.4*x+.1*z})),
 make('water',()=>({terrain:'water',height:1}))];
 for(const c of a)c.rays=[[[.5,2.65,2.5],[4.5,2.7,2.5]],[[4.5,2.7,2.5],[.5,2.65,2.5]],
 [[.5,12,2.5],[4.5,12,2.5]],[[.5,2.65,.5],[4.5,2.7,4.5]],[[.5,c.tiles[2][0].height+1.65,2.5],[4.5,c.tiles[2][4].height+1.7,2.5]]];
 return a;
}
async function makeGolden(){
 const [terrain,line]=await modules;
 return {source:'locationTerrain.js tileSurfaceTriangles, actual renderer geometry',cases:cases().map(c=>({...c,
  hits:c.rays.map(([a,b])=>line.terrainLineHit(c.tiles,a,b)),triangles:c.tiles[2][2].terrain==='water'?[]:terrain.tileSurfaceTriangles(c.tiles,2,2)}))};
}
if(process.argv.includes('--write-fixture')){
 makeGolden().then(data=>{fs.mkdirSync(path.dirname(fixturePath),{recursive:true});fs.writeFileSync(fixturePath,JSON.stringify(data));console.log('Фиксированная renderer parity fixture сохранена.');});
}else{
 test('Golden остаётся привязан к настоящей геометрии renderer',async()=>{
  assert.deepEqual(await makeGolden(),JSON.parse(fs.readFileSync(fixturePath,'utf8')));
 });
 test('Холм и песчаный гребень перекрывают луч, высокий луч и вода его не блокируют',async()=>{
  const [,line]=await modules,all=cases();
  for(const name of ['hill','sand']){const c=all.find(c=>c.name===name);assert.ok(line.terrainLineHit(c.tiles,...c.rays[0]));assert.equal(line.terrainLineHit(c.tiles,...c.rays[2]),null);}
  const water=all.find(c=>c.name==='water');assert.equal(line.terrainLineHit(water.tiles,...water.rays[0]),null);
 });
 test('Открытый/закрытый люк сохраняет семантику opening_at',async()=>{
  const [,line]=await modules,c=cases().find(c=>c.name==='hill');
  const hatch={type:'hatch',tile_x:2,tile_y:2,properties:{level:0,is_open:true,dimensions:{width:5,depth:5}}};
  const accept=hit=>!line.terrainOpeningAt([hatch],0,hit.x,hit.z);
  assert.equal(line.terrainLineHit(c.tiles,...c.rays[0],new Map(),accept),null);
  hatch.properties.is_open=false;assert.ok(line.terrainLineHit(c.tiles,...c.rays[0],new Map(),accept));
 });
 test('Реальная клиентская hasLineOfSight использует треугольники холма',async()=>{
  const [,line]=await modules,c=cases().find(c=>c.name==='hill');
  const scene=fs.readFileSync(path.join(root,'app/static/js/locationScene.js'),'utf8'),start=scene.indexOf('function hasLineOfSight('),end=scene.indexOf('function isPointVisibleToPlayer(',start);
  const index={walls:[],terrainCache:new Map()};
  const context=vm.createContext({terrainLineHit:line.terrainLineHit,terrainOpeningAt:line.terrainOpeningAt,currentLocationData:{tiles_data:c.tiles,objects:[]},
   getTileHeight:(x,y)=>c.tiles[y][x].height,getVisionObjectIndex:()=>index,getVisionBlockerHeight:()=>-Infinity,lightPathOpen:()=>true});
  vm.runInContext(scene.slice(start,end),context);
  assert.equal(context.hasLineOfSight(0,2,4,2),false);
  assert.equal(context.hasLineOfSight(0,2,4,2,12,12),true);
 });
}
