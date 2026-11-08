// Реакции на горение и истечение срока через настоящий бой на копии Припяти.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {loadFixture,waitForDocument}=require('./durable_reload.browser.cjs');
module.exports=async({page,request,token,lobby,base,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'artifacts');assert.ok(safeSession.verified);
    const api=`/lobbies/${lobby.id}`,{fixture}=loadFixture();
    const rules=JSON.parse(fs.readFileSync(path.join(__dirname,'../app/data/world_rules.json'),'utf8')).artifacts;
    const location=await request(api+'/locations',{...fixture,objects:undefined,world_tile_x:7,world_tile_z:8},token),loc=api+'/locations/'+location.id;
    await request(loc+'/objects/batch',{objects:fixture.objects},token);
    const rule=rules.find(a=>a.name==='Пламень крови');assert.ok(rule);
    const actor=await request(api+'/characters',{name:'Проверка реактивного артефакта',data:{
        equipment:{armor:{containers:[{item:{id:'thermal-reaction',category:'artifact',name:rule.name,attributes:rule}}]}},
        health:{current:700,max:700,effects:[{type:'burning',name:'Горение',remaining:1,tick:'round_end',source:'artifact-browser',damage_per_round:50}]}}},token);
    const dummy=await request(api+'/characters',{name:'Второй участник проверки',data:{health:{current:700,max:700}}},token);
    const first=await request(loc+'/spawn_character',{character_id:actor.id,tile_x:42,tile_y:48},token);
    const second=await request(loc+'/spawn_character',{character_id:dummy.id,tile_x:44,tile_y:48},token);
    const href=base+api+'/page?location='+location.id,epoch=await page.evaluate('performance.timeOrigin');
    await page.call('Page.navigate',{url:href});await waitForDocument(page,{href,afterEpoch:epoch,label:'Артефакты на копии Припяти'});
    await page.until(`window.currentLocationId===${location.id}&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Припять готова');
    await page.evaluate(`window.openCharacterSheet(${actor.id},'basic').then(()=>true)`);
    await request(loc+'/combat/start',{location_character_ids:[first.location_character_id,second.location_character_id],
        initiator_location_character_id:first.location_character_id,initiator_first:true},token);
    const get=()=>request('/lobbies/characters/'+actor.id,null,token,'GET');
    const reactions=d=>(d.health.effects||[]).filter(e=>e.source?.startsWith('artifact-reaction:'));
    const before=await get();assert.equal(reactions(before.data).length,0);
    const round=async()=>{
        await request(loc+'/combat/end_turn',{location_character_id:first.location_character_id},token);
        await request(loc+'/combat/end_turn',{location_character_id:second.location_character_id},token);
    };
    await round();const hit=await get(),active=reactions(hit.data);assert.equal(active.length,1);assert.equal(active[0].value,-1);
    assert.equal(before.data.health.current-hit.data.health.current,50);
    await page.until("document.getElementById('character-sheet-modal')?.innerText.includes('Пламень крови: реакция на термический урон')",'Реакция появилась в листе по игровому событию');
    assert.equal(hit.data._artifact_profile.movement_penalty,-1);
    let changed=await get();changed.data.health.effects=changed.data.health.effects.filter(e=>!e.source?.startsWith('artifact-reaction:'));
    await request('/lobbies/characters/'+actor.id,{data:changed.data},token,'PUT');
    assert.equal(reactions((await get()).data).length,1,'Сохранение листа не удаляет серверный эффект');
    await round();const next=await get();assert.equal(reactions(next.data)[0].remaining_seconds,active[0].remaining_seconds-6);
    await round();await round();const expired=await get();assert.equal(reactions(expired.data).length,0);assert.equal(expired.data._artifact_profile.movement_penalty,0);
    await page.until("!document.getElementById('character-sheet-modal')?.innerText.includes('Пламень крови: реакция на термический урон')",'Истёкшая реакция исчезла из листа');
    await request(loc+'/combat/end',{},token);
    fs.writeFileSync(path.join(safeSession.artifacts,'artifact-reactions.json'),JSON.stringify({scene:'Припять',artifact:rule.name,
        damage:50,initialSeconds:active[0].remaining_seconds,nextSeconds:reactions(next.data)[0].remaining_seconds,
        persistedAfterSave:true,expired:true,networkVisible:true},null,2));
    console.log('Артефакты: настоящее горение, временный штраф, сохранение, сетевое обновление и истечение по раундам проверены в Припяти.');
};
