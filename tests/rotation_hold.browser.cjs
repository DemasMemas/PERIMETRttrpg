// №97: только собственная файловая БД через фиксированный безопасный launcher.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const pause=ms=>new Promise(r=>setTimeout(r,ms));
module.exports=async({page,request,token,lobby,safeSession})=>{
 assert.equal(process.env.VTT_SAFE_SCENARIO,'rotation');assert.ok(safeSession);
 const evidence={card:97,checks:[]};
 const save=()=>fs.writeFileSync(path.join(safeSession.artifacts,'rotation.json'),JSON.stringify(evidence,null,2));
 const key=async(code,type)=>page.call('Input.dispatchKeyEvent',{type,code,key:code==='Escape'?'Escape':code.slice(-1).toLowerCase()});
 const shot=async name=>fs.writeFileSync(path.join(safeSession.artifacts,name+'.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
 const settled=async()=>{await page.until("!document.getElementById('world-selection-editor')?.disabled",'world save');await pause(80);};
 const turn=async(label,read,settle=async()=>{},direction='KeyQ')=>{
  await page.evaluate('document.activeElement?.blur()');
  const before=await page.evaluate(read);
  await key(direction,'keyDown');await key(direction,'keyUp');await settle();
  const tap=await page.evaluate(read),distance=(a,b)=>Math.abs(((a-b+540)%360)-180);
  assert.ok(Math.abs(distance(tap,before)-15)<.001,label+' tap=15');
  await key(direction,'keyDown');await pause(420);
  const during=await page.evaluate(read);await pause(120);
  const during2=await page.evaluate(read);
  await key(direction,'keyUp');await settle();
  const released=await page.evaluate(read);await pause(300);await settle();const stopped=await page.evaluate(read);
  assert.ok(distance(during,tap)>15,label+' held changed beyond initial step');
  assert.ok(distance(during2,during)>.01,label+' continued while held');
  assert.ok(distance(stopped,released)<.001,label+' stopped after keyup');
  evidence.checks.push({label,before,tap,during,during2,released,stopped});save();
 };
 try{
  await page.call('Page.bringToFront');
  await page.evaluate('sceneModule.controls.target.set(31.5,1,10.5);sceneModule.camera.position.set(31.5,23,32);sceneModule.controls.update()');
  await page.evaluate("(async()=>{window.M=await import('/static/js/mapEdit.js');M.setEditMode(true);M.selectWorldEditorTool('objects');M.selectWorldObject('factory');document.activeElement?.blur()})()");
  const worldPreview="Number(document.getElementById('world-object-rotation-number').value)";
  await turn('world-placement',worldPreview);
  await page.evaluate('(async()=>{const N=await import("/static/js/worldNumericInputs.js");N.syncWorldNumber("world-object-rotation",344.5);document.activeElement?.blur()})()');
  await key('KeyQ','keyDown');await key('KeyQ','keyUp');
  assert.equal(await page.evaluate(worldPreview),359.5,'№97 fractional wrap keeps15');
  await key('KeyQ','keyDown');await key('KeyQ','keyUp');
  assert.equal(await page.evaluate(worldPreview),14.5,'№97 full circle wrap keeps15');
  evidence.checks.push({label:'world-placement-fractional-wrap',start:344.5,tap:359.5,next:14.5});save();

  await shot('rotation-world-placement');
  await page.evaluate("M.selectWorldEditorTool('select');selectDirectWorldObjects([{chunkX:0,chunkY:0,tileX:31,tileY:10,index:0}]);document.activeElement?.blur()");
  const world="getDirectWorldObjectSelection()[0].object.rotation||0";
  await turn('world-selected',world,settled);
  await page.evaluate("selectDirectWorldObjects([{chunkX:0,chunkY:0,tileX:31,tileY:10,index:0},{chunkX:0,chunkY:0,tileX:31,tileY:10,index:1}]);document.activeElement?.blur()");
  const groupBefore=await page.evaluate('getDirectWorldObjectSelection()');
  await turn('world-group',world,settled);
  const groupAfter=await page.evaluate('getDirectWorldObjectSelection()');
  assert.equal(groupAfter.length,2);assert.equal(groupBefore.length,2);
  const delta=(groupAfter[0].object.rotation-groupBefore[0].object.rotation+360)%360;
  assert.ok(Math.abs(((groupAfter[1].object.rotation||0)-(groupBefore[1].object.rotation||0)+360)%360-delta)<.001);
  await shot('rotation-world-group');
  const loc=await request('/lobbies/'+lobby.id+'/locations',{name:'№97 isolated editor',world_tile_x:8,world_tile_z:8,grid_width:16,grid_height:16,
   tiles_data:Array.from({length:16},()=>Array.from({length:16},()=>({terrain:'concrete',height:1,objects:[]})))},token);
  const api='/lobbies/'+lobby.id+'/locations/'+loc.id;
  await request(api+'/objects/batch',{objects:[7,9,12].map(x=>({type:'box',name:'Rotation '+x,tile_x:x,tile_y:8,properties:{dimensions:{width:1.5,depth:1,height:1},rotation:0}}))},token);
  await page.evaluate("(async()=>{window.L=await import('/static/js/locationScene.js');window.UI=await import('/static/js/ui_interactions.js');window.T=await import('three');await enterLocation("+loc.id+")})()");
  await page.until("window.locationControls&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')",'local scene');
  await page.evaluate("L.setLocationEditMode(true);selectLocationEditorTool('assets');UI.expandMapToolsPanel();const q=document.querySelector('.location-asset-search');q.value='Стол';q.dispatchEvent(new Event('input',{bubbles:true}))");
  await page.until("document.querySelector('.location-asset-card')",'table preset');
  await page.evaluate("document.querySelector('.location-asset-card').click();UI.collapseMapToolsPanel();locationControls.target.set(8,1,8);locationControls.object.position.set(8,15,19);locationControls.update();document.activeElement?.blur()");
  // Отслеживаем положение реального объекта через обычный API выбранного пресета.
  const placement="Number(document.querySelector('[data-placement-rotation]')?.value ?? document.getElementById('loc-structure-rotation')?.value)";
  // Существующий UI синхронизирует угол в placement controls.
  const fields=await page.evaluate("[...document.querySelectorAll('input')].filter(n=>/rotation|azimuth/.test(n.id+' '+n.outerHTML)).map(n=>({id:n.id,html:n.outerHTML.slice(0,250)}))");
  evidence.fields=fields;save();
  await turn('location-placement',placement);
  await shot('rotation-location-placement');
  await page.evaluate("cancelLocationAssetPlacement();selectLocationEditorTool('select');document.activeElement?.blur();window.rotationSelectionEvents=[];window.addEventListener('location-editor-selection',e=>rotationSelectionEvents.push(e.detail))");
  const clickObject=async(x,shift=false)=>{
   const point=await page.evaluate("(()=>{locationControls.object.updateMatrixWorld(true);const p=new T.Vector3("+x+".5,1.5,8.5).project(locationControls.object),r=document.querySelector('#location-canvas canvas').getBoundingClientRect();return{x:r.x+(p.x+1)*r.width/2,y:r.y+(1-p.y)*r.height/2}})()");
   if(shift)await page.call('Input.dispatchKeyEvent',{type:'keyDown',code:'ShiftLeft',key:'Shift',modifiers:8});
   for(const type of ['mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{type,...point,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1,modifiers:shift?8:0});
   if(shift)await page.call('Input.dispatchKeyEvent',{type:'keyUp',code:'ShiftLeft',key:'Shift'});
  };
  const localSettled=async()=>{await page.until("!L.getLocationEditorHistoryState().busy",'local saved');await pause(200);};
  await clickObject(7);await page.until("L.getSelectedLocationEditorAsset()?.structureType==='box' && L.getSelectedLocationEditorAsset()?.x===7.5",'select local box');
  await turn('location-selected',"L.getSelectedLocationEditorAsset().rotation",localSettled);
  await clickObject(9,true);
  assert.equal((await page.evaluate('rotationSelectionEvents.at(-1)')).selectedCount,2);
  await turn('location-group',"L.getSelectedLocationEditorAsset().rotation",localSettled);
  const stored=await request(api,null,token,'GET');evidence.stored=stored.objects.map(o=>({id:o.id,name:o.name,x:o.tile_x,y:o.tile_y,rotation:o.properties.rotation}));
  assert.equal(evidence.stored.find(o=>o.name==='Rotation 12').rotation,0,'unselected unchanged');
  await shot('rotation-location-group');
  const epoch=await page.evaluate('performance.timeOrigin'),href=await page.evaluate('location.href');
  await page.call('Page.reload');
  await require('./durable_reload.browser.cjs').waitForDocument(page,{href,afterEpoch:epoch,label:'№97 сохранение после reload'});
  await page.until('window.enterLocation&&window.toggleEditMode','startup after reload');
  await page.evaluate('(async()=>{window.L=await import("/static/js/locationScene.js");await enterLocation('+loc.id+')})()');
  await page.until('window.locationControls&&document.getElementById("scene-loading-screen").classList.contains("is-hidden")','local scene after reload');
  const reloaded=await request(api,null,token,'GET');
  assert.deepEqual(reloaded.objects.map(o=>({id:o.id,name:o.name,x:o.tile_x,y:o.tile_y,rotation:o.properties.rotation})),evidence.stored);
  evidence.reload={beforeEpoch:epoch,afterEpoch:await page.evaluate('performance.timeOrigin'),persisted:true};
  evidence.status='READY_FOR_REVIEW';save();
 }catch(e){evidence.status='FAIL';evidence.error=e.message;save();throw e;}
};
