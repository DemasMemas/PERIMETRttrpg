const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,safeSession})=>{
 assert.equal(process.env.VTT_SAFE_SCENARIO,'height');assert.ok(safeSession);
 const checks=[],api='/lobbies/'+lobby.id;
 const save=()=>fs.writeFileSync(path.join(safeSession.artifacts,'height.json'),JSON.stringify({checks},null,2));
 const actor=await request(api+'/characters',{name:'№101 изолированный персонаж',data:{}},token);
 for(const [rise,allowed] of [[.99,true],[1,false],[1.99,false],[2,false]]){
  const location=await request(api+'/locations',{name:'№101 перепад '+rise,world_tile_x:5+checks.length,world_tile_z:6,grid_width:5,grid_height:5,
   tiles_data:Array.from({length:5},()=>Array.from({length:5},(_,x)=>({terrain:'concrete',height:4+(x>=2?rise:0),objects:[]})))},token);
  const url=api+'/locations/'+location.id;
  await request(url+'/spawn_character',{character_id:actor.id,tile_x:1,tile_y:2},token);
  await page.evaluate('(async()=>{window.L=await import("/static/js/locationScene.js");await enterLocation('+location.id+')})()');
  await page.until('(()=>{try{return window.currentLocationId==='+location.id+'&&window.locationControls&&L.characterPose('+actor.id+')&&document.getElementById("scene-loading-screen").classList.contains("is-hidden")}catch{return false}})()','сцена перепада');
  await page.evaluate('L.setLocationEditMode(false);locationControls.target.set(2.5,4,2.5);locationControls.object.position.set(7,13,11);locationControls.update()');
  const route=(await request(url+'/movement-preview',{character_id:actor.id,x:2,y:2,floor_level:0},token)).route;
  assert.equal(Boolean(route&&!route.special_action),allowed,'предпросмотр обычного шага '+rise);
  if(rise>=1&&rise<2)assert.equal(route.special_action,'climb_up','требуется отдельное действие подъёма');
  const result=await page.evaluate('(async()=>{const id='+actor.id+';return await new Promise((resolve,reject)=>{let timer;const clear=()=>{clearTimeout(timer);socket.off("character_moved",moved);socket.off("movement_rejected",rejected)};const moved=p=>{if(p.character_id!==id)return;clear();resolve({event:"moved",x:p.x,y:p.y,height:p.height})};const rejected=p=>{if(p.character_id!==id)return;clear();resolve({event:"rejected",x:p.x,y:p.y,message:p.message})};socket.on("character_moved",moved);socket.on("movement_rejected",rejected);timer=setTimeout(()=>{clear();reject(Error("Нет ответа movement"))},5000);socket.emit("move_in_location",{token:localStorage.getItem("access_token"),location_id:'+location.id+',character_id:id,x:2,y:2,movement_mode:"walk",floor_level:0})})})()');
  assert.equal(result.event,allowed?'moved':'rejected','живое движение '+rise);
  await page.until('L.characterPose('+actor.id+').x==='+(allowed?2:1),'фактическая позиция');
  checks.push({rise,allowed,route,result,pose:await page.evaluate('L.characterPose('+actor.id+')')});save();
  if(rise===2)fs.writeFileSync(path.join(safeSession.artifacts,'height-2m-blocked.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
 }
 fs.writeFileSync(path.join(safeSession.artifacts,'height.json'),JSON.stringify({status:'READY_FOR_REVIEW',checks},null,2));
};
