const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession})=>{
 assert.equal(process.env.VTT_SAFE_SCENARIO,'gm-relocation');assert.ok(safeSession);
 const result={checks:[]},save=()=>fs.writeFileSync(path.join(safeSession.artifacts,'gm-relocation.json'),JSON.stringify(result,null,2));
 const api='/lobbies/'+lobby.id,password='synthetic-gm-relocation-password';
 await request('/auth/register',{username:'relocation-owner',email:'relocation-owner@example.test',password});
 const owner=await request('/auth/login',{username:'relocation-owner',password});
 await request(api+'/join',{},owner.access_token);
 const actor=await request(api+'/characters',{name:'№104 тестовый персонаж',data:{}},owner.access_token);
 const location=await request(api+'/locations',{name:'№104 изолированная сцена',world_tile_x:7,world_tile_z:7,grid_width:5,grid_height:5,
  tiles_data:Array.from({length:5},()=>Array.from({length:5},()=>({terrain:'concrete',height:1,objects:[]})))},token);
 const url=api+'/locations/'+location.id;
 await request(url+'/objects',{name:'Верхний пол',type:'floor',tile_x:2,tile_y:2,properties:{level:1,dimensions:{width:5,depth:5,height:.12}}},token);
 const spawn=await request(url+'/spawn_character',{character_id:actor.id,tile_x:1,tile_y:1,floor_level:1,assign_to_user_id:owner.user_id},token);
 const version=await(await fetch('http://127.0.0.1:'+debugPort+'/json/version')).json(),protocol=await connect(version.webSocketDebuggerUrl);
 let peer,context;
 const mount=async client=>{
  await client.evaluate('(async()=>{window.L=await import("/static/js/locationScene.js");await enterLocation('+location.id+')})()');
  await client.until('(()=>{try{return window.currentLocationId==='+location.id+'&&L.characterPose('+actor.id+')&&document.getElementById("scene-loading-screen").classList.contains("is-hidden")}catch{return false}})()','GM scene');
  await client.evaluate('window.gmRelocationEvents=[];socket.on("character_spawned",e=>gmRelocationEvents.push({action:e.action,id:e.character.id,x:e.character.pos_x,y:e.character.pos_y,controlled_by:e.character.controlled_by,floor:e.character.floor_level,height:e.character.height}));locationControls.target.set(2.5,3.4,2.5);locationControls.object.position.set(8,12,11);locationControls.update();true');
 };
 try{
  context=await protocol.call('Target.createBrowserContext');
  const target=await protocol.call('Target.createTarget',{url:base,browserContextId:context.browserContextId});
  const targets=await(await fetch('http://127.0.0.1:'+debugPort+'/json')).json();
  peer=await connect(targets.find(t=>t.id===target.targetId).webSocketDebuggerUrl);
  await require('./durable_reload.browser.cjs').waitForDocument(peer,{href:base,label:'owner origin'});
  await peer.evaluate('localStorage.setItem("access_token",'+JSON.stringify(owner.access_token)+');localStorage.setItem("user_id",'+JSON.stringify(String(owner.user_id))+')');
  await peer.call('Page.navigate',{url:base+'/lobbies/'+lobby.id+'/page'});
  await peer.until('window.enterLocation&&!window.isGM','owner startup');
  await mount(page);await mount(peer);
  await request(url+'/combat/start',{location_character_ids:[spawn.location_character_id],initiator_location_character_id:spawn.location_character_id,initiator_first:true},token);
  const before=await request(url+'/combat',null,token,'GET'),beforeActor=before.characters.find(c=>c.character_id===actor.id);
  const denied=await fetch(base+url+'/spawn_character',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+owner.access_token},body:JSON.stringify({character_id:actor.id,tile_x:2,tile_y:1})});
  await denied.arrayBuffer();assert.equal(denied.status,403);
  result.checks.push({stage:'owner-denied',status:denied.status});save();
  for(const [x,y,floor] of [[2,1,1],[2,2,0]]){
   const payload={character_id:actor.id,tile_x:x,tile_y:y};if(floor===0)payload.floor_level=0;
   await request(url+'/spawn_character',payload,token);
   for(const client of [page,peer])await client.until('L.characterPose('+actor.id+').x==='+x+'&&L.characterPose('+actor.id+').y==='+y+'&&gmRelocationEvents.some(e=>e.id==='+actor.id+'&&e.x==='+x+'&&e.y==='+y+'&&e.floor==='+floor+'&&e.controlled_by==='+owner.user_id+')','двухклиентное GM-перемещение');
   const after=await request(url+'/combat',null,token,'GET'),c=after.characters.find(c=>c.character_id===actor.id);
   for(const field of ['action_points_current','movement_points_current','free_actions_current'])assert.equal(c[field],beforeActor[field],field+' unchanged');
   assert.equal(c.floor_level,floor);assert.equal(c.controlled_by,owner.user_id);
   const clientStates=await Promise.all([page,peer].map(client=>client.evaluate('L.getCombatState().characters.find(c=>c.character_id==='+actor.id+')')));
   for(const state of clientStates)assert.equal(state.floor_level,floor,'combat event floor sync');
   result.checks.push({stage:'gm-moved',x,y,floor,ap:c.action_points_current,op:c.movement_points_current,controller:c.controlled_by,clients:await Promise.all([page,peer].map(client=>client.evaluate('({pose:L.characterPose('+actor.id+'),events:gmRelocationEvents,connected:socket.connected,transport:socket.io.engine.transport.name})')))});save();
  }
  await page.call('Page.bringToFront');
  fs.writeFileSync(path.join(safeSession.artifacts,'gm-relocation.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
  await request(url+'/combat/end',{},token);
  const fixture=JSON.parse(fs.readFileSync(path.resolve(__dirname,'../tmp/pripyat-regression.json'),'utf8'));
  const pripyat=await request(api+'/locations',{...fixture,objects:undefined,name:'Припять — перенос ГМа',world_tile_x:8,world_tile_z:8},token);
  await request(api+'/locations/'+pripyat.id+'/objects/batch',{objects:fixture.objects},token);
  await request(api+'/locations/'+pripyat.id+'/spawn_character',{character_id:actor.id,tile_x:41,tile_y:48,assign_to_user_id:owner.user_id},token);
  for(const client of [page,peer]){
   await client.evaluate('(async()=>{await enterLocation('+pripyat.id+')})()');
   await client.until('window.currentLocationId==='+pripyat.id+'&&document.getElementById("scene-loading-screen").classList.contains("is-hidden")','копия Припяти для переноса');
  }
  await page.evaluate('(async()=>{window.T=await import("three")})()');
  await require('./gm_transfer_ui.browser.cjs')({page,peer,request,token,lobby,safeSession,location:pripyat,actor,player:owner});
  await require('./zombie_restore.browser.cjs')({page,peer,request,token,lobby,safeSession,location:pripyat,player:owner});
  result.status='READY_FOR_REVIEW';save();
 }catch(e){result.status='FAIL';result.error=e.message;save();throw e;}
 finally{if(peer)await peer.close();if(context)await protocol.call('Target.disposeBrowserContext',{browserContextId:context}).catch(()=>{});await protocol.close();}
};
