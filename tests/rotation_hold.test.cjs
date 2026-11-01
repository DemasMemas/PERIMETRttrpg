const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const modulePromise=import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync(path.join(__dirname,'../app/static/js/rotationHold.js'),'utf8')).toString('base64'));
async function harness(){
 const {createRotationHold}=await modulePromise;
 let time=0,next=0,context='scene:a:selected:1',enabled=true;
 const frames=new Map(),deltas=[];
 const h=createRotationHold({now:()=>time,requestFrame:fn=>{frames.set(++next,fn);return next;},cancelFrame:id=>frames.delete(id),allowed:()=>enabled});
 const rotate=(direction,fraction)=>deltas.push(direction*fraction*15);
 const start=(code='KeyE',repeat=false)=>h.start({code,repeat},rotate,()=>context);
 const tick=t=>{time=t;const pending=[...frames.values()];frames.clear();pending.forEach(fn=>fn(t));};
 return {h,start,tick,deltas,frames,setContext:v=>context=v,setEnabled:v=>enabled=v};
}
test('Короткое нажатие не добавляет угол к исходному шагу 15 градусов',async()=>{
 const h=await harness();h.start();h.tick(16);h.tick(100);h.h.release('KeyE');h.tick(300);
 assert.deepEqual(h.deltas,[]);assert.equal(h.frames.size,0);
});
test('Удержание даёт дробные углы независимо от частоты кадров и останавливается без шага на отпускании',async()=>{
 const h=await harness();h.start();for(let t=10;t<=680;t+=10)h.tick(t);
 assert.ok(Math.abs(h.deltas.reduce((a,b)=>a+b,0)-45)<1e-8);
 assert.ok(h.deltas.every(v=>v>0&&v<15));
 const before=h.deltas.length;h.h.release('KeyE');h.tick(1000);assert.equal(h.deltas.length,before);
});
test('Q вращает в обратную сторону; OS-repeat не создаёт второй цикл',async()=>{
 const h=await harness();h.start('KeyQ');h.start('KeyQ',true);assert.equal(h.frames.size,1);
 for(let t=20;t<=400;t+=20)h.tick(t);assert.ok(h.deltas.every(v=>v<0));
});
test('Отпускание другой клавиши не останавливает активную; смена Q/E не оставляет старого цикла',async()=>{
 const h=await harness();h.start('KeyQ');h.h.release('KeyE');assert.equal(h.frames.size,1);
 h.start('KeyE');assert.equal(h.frames.size,1);h.tick(200);assert.ok(h.deltas.every(v=>v>0));
});
test('Изменение выделения/сцены останавливает удержание до следующего изменения',async()=>{
 for(const value of ['scene:b:selected:1','scene:a:selected:2',null]){
  const h=await harness();h.start();h.setContext(value);h.tick(300);assert.deepEqual(h.deltas,[]);assert.equal(h.frames.size,0);
 }
});
test('Потеря допуска и явная отмена очищают запланированный кадр',async()=>{
 const h=await harness();h.start();h.setEnabled(false);h.tick(300);assert.deepEqual(h.deltas,[]);
 h.setEnabled(true);h.start();h.h.stop();h.tick(400);assert.equal(h.frames.size,0);assert.deepEqual(h.deltas,[]);
});
test('После паузы кадра нет большого скачка; отказ вращения завершает цикл',async()=>{
 const h=await harness();h.start();h.tick(10000);assert.deepEqual(h.deltas,[4.5]);
 h.h.stop();h.h.start({code:'KeyE'},()=>false,()=>1);h.tick(10300);assert.equal(h.frames.size,0);
});
test('Браузерные события blur, pagehide, focus, pointer, Escape и visibility освобождают удержание',async()=>{
 const {initMapRotationHold}=await modulePromise,events={},docEvents={},frames=new Map();let t=0,id=0,changes=0;
 const old={window:global.window,document:global.document,requestAnimationFrame:global.requestAnimationFrame,cancelAnimationFrame:global.cancelAnimationFrame};
 try{
  global.window={addEventListener:(name,fn)=>events[name]=fn};
  global.document={hidden:false,activeElement:{},querySelectorAll:()=>[],addEventListener:(name,fn)=>docEvents[name]=fn};
  global.requestAnimationFrame=fn=>{frames.set(++id,fn);return id;};global.cancelAnimationFrame=id=>frames.delete(id);
  const h=initMapRotationHold(()=>false);
  for(const cancel of [()=>events.blur(),()=>events.pagehide(),()=>docEvents.focusin(),()=>docEvents.pointerdown(),()=>docEvents.visibilitychange(),()=>events.keydown({code:'Escape'})]){
   h.start({code:'KeyQ'},()=>changes++,()=>1);assert.equal(frames.size,1);cancel();assert.equal(frames.size,0);
  }
  assert.equal(changes,0);
 }finally{Object.assign(global,old);}
});

const vm=require('node:vm');
async function localPreviewHarness(){
 const source=fs.readFileSync(path.join(__dirname,'../app/static/js/locationScene.js'),'utf8');
 const first=source.indexOf('function previewLocationRotationHold('),last=source.indexOf('export function rotateLocationPlacement(',first);
 const calls=[],meshes=[],removed=[],data={id:1},scene={add:m=>meshes.push(m),remove:m=>removed.push(m)};
 const c=vm.createContext({window:{isGM:true,locationEditMode:true,locationEditorTool:'select'},currentLocationData:data,houseSceneEpoch:1,
  selectedLocationAsset:null,editorSelections:[{kind:'structure',id:7}],editorRotateMode:false,editorMoveMode:false,editorResizeMode:false,
  editorHeldRotation:null,editorHotkeyRotationQueue:Promise.resolve(),scene,
  createEditorGroupMovePreview:()=>({children:[{rotation:{y:0},userData:{editorMoveOrigin:{rotation:0}}}]}),
  updateSelectedLocationAssetGroup:async transform=>{calls.push(transform({kind:'structure',rotation:30}));return true;},
  disposeLocationObject:m=>m.disposed=true,showNotification:()=>{}});
 vm.runInContext(source.slice(first,last).replaceAll('export ',''),c);
 return {c,calls,meshes,removed,data};
}
test('Подлокация: дробное удержание меняет preview без сетевой очереди, отпускание сохраняет один итог',async()=>{
 const h=await localPreviewHarness();h.c.previewLocationRotationHold(1.5);h.c.previewLocationRotationHold(2.25);
 await h.c.editorHeldRotation.ready;
 assert.equal(h.calls.length,0);assert.equal(h.meshes.length,1);
 assert.ok(Math.abs(h.meshes[0].children[0].rotation.y-3.75*Math.PI/180)<1e-9);
 h.c.finishLocationRotationHold(true);await h.c.editorHotkeyRotationQueue;
 assert.equal(h.calls.length,1);assert.equal(h.calls[0].rotation,26.25);assert.equal(h.removed.length,1);
});
test('Подлокация: отмена удержания удаляет preview без сохранения',async()=>{
 const h=await localPreviewHarness();h.c.previewLocationRotationHold(20);await h.c.editorHeldRotation.ready;
 h.c.finishLocationRotationHold(false);await h.c.editorHotkeyRotationQueue;
 assert.equal(h.calls.length,0);assert.equal(h.removed.length,1);
});
test('Подлокация: смена сцены перед сохранением не записывает старую группу',async()=>{
 const h=await localPreviewHarness();h.c.previewLocationRotationHold(20);await h.c.editorHeldRotation.ready;
 h.c.currentLocationData={id:2};h.c.finishLocationRotationHold(true);await h.c.editorHotkeyRotationQueue;assert.equal(h.calls.length,0);
});
