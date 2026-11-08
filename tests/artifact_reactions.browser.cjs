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
    assert.equal(before.data.health.current-hit.data.health.current,55);
    await page.until("document.getElementById('character-sheet-modal')?.innerText.includes('Пламень крови: реакция на термический урон')",'Реакция появилась в листе по игровому событию');
    assert.equal(hit.data._artifact_profile.movement_penalty,-1);
    let changed=await get();changed.data.health.effects=changed.data.health.effects.filter(e=>!e.source?.startsWith('artifact-reaction:'));
    await request('/lobbies/characters/'+actor.id,{data:changed.data},token,'PUT');
    assert.equal(reactions((await get()).data).length,1,'Сохранение листа не удаляет серверный эффект');
    await round();const next=await get();assert.equal(reactions(next.data)[0].remaining_seconds,active[0].remaining_seconds-6);
    await round();await round();const expired=await get();assert.equal(reactions(expired.data).length,0);assert.equal(expired.data._artifact_profile.movement_penalty,0);
    await page.until("!document.getElementById('character-sheet-modal')?.innerText.includes('Пламень крови: реакция на термический урон')",'Истёкшая реакция исчезла из листа');
    await request(loc+'/combat/end',{},token);
    const skin=rules.find(a=>a.name==='Вторая кожа'),skinCharacter=await get();assert.ok(skin);
    skinCharacter.data.equipment={armor:{containers:[{item:{id:'skin-artifact',category:'artifact',name:skin.name,attributes:skin}}]}};
    skinCharacter.data.health.painLevel=7;skinCharacter.data.health.combatMeta.painIncreased=false;
    await request('/lobbies/characters/'+actor.id,{data:skinCharacter.data},token,'PUT');
    await page.evaluate(`window.openCharacterSheet(${actor.id},'basic').then(()=>true)`);
    await request(loc+'/combat/start',{location_character_ids:[first.location_character_id,second.location_character_id],
        initiator_location_character_id:first.location_character_id,initiator_first:true},token);
    await request(loc+'/combat/end_turn',{location_character_id:first.location_character_id},token);
    assert.equal((await get()).data.health.painLevel,7);
    await request(loc+'/combat/end_turn',{location_character_id:second.location_character_id},token);
    assert.equal((await get()).data.health.painLevel,5);
    await page.until("Number(document.querySelector('[name=\"health.painLevel\"]')?.value)===5",'Снижение боли Второй кожи видно в листе');
    await request(loc+'/combat/end',{},token);
    const shard=rules.find(a=>a.name==='Пылающий осколок');assert.ok(shard);
    const gun=await get();gun.data.weapons=[{id:'shard-weapon',name:'Контрольное оружие',range:30,ergonomics:100,damage:5,
        ammo:20,durability:100,maxDurability:100,installedMagazine:{id:'shard-mag',name:'Контрольный магазин',ammo:[{
            id:'shard-ammo',name:'Контрольный патрон',quantity:20,damage:5,attributes:{caliber:'9x18',damage:5}}]}}];
    gun.data.skills={physical:{shooting:{base:100},strength:{base:20}}};
    await request('/lobbies/characters/'+actor.id,{data:gun.data},token,'PUT');
    const victim=await request('/lobbies/characters/'+dummy.id,null,token,'GET');
    victim.data.equipment={armor:{containers:[{item:{id:'shard-artifact',category:'artifact',name:shard.name,attributes:shard}}]}};
    await request('/lobbies/characters/'+dummy.id,{data:victim.data},token,'PUT');
    await request(loc+'/characters/'+actor.id+'/facing',{facing_x:1,facing_y:0},token,'PATCH');
    await request(loc+'/characters/'+dummy.id+'/facing',{facing_x:-1,facing_y:0},token,'PATCH');
    await page.evaluate(`window.openCharacterSheet(${dummy.id},'basic').then(()=>true)`);
    await request(loc+'/combat/start',{location_character_ids:[first.location_character_id,second.location_character_id],
        initiator_location_character_id:first.location_character_id,initiator_first:true},token);
    const command={location_character_id:first.location_character_id,weapon_index:0,target_character_id:dummy.id};
    await request(loc+'/combat/action',{...command,action_key:'draw_weapon'},token);await round();
    await request(loc+'/combat/action',{...command,action_key:'aim'},token);
    const state=await request(loc+'/combat',null,token,'GET'),cost=state.current_character.weapon_ergonomics[0].aimed_shot_action_points;
    const prior=await request('/lobbies/characters/'+dummy.id,null,token,'GET');
    const shot=await request(loc+'/combat/action',{...command,action_key:'attack',fire_mode:'aimed',target_zone:'chest',shot_count:1,volley_count:1,action_points:cost},token);
    const hitResult=shot.attack.results[0];assert.ok(hitResult.hit);assert.equal(hitResult.artifact_reactions.length,1);
    const backlash=hitResult.artifact_reactions[0];assert.equal(backlash.roll,50);assert.equal(backlash.raw_damage,50);assert.equal(backlash.damage,40);
    const after=await request('/lobbies/characters/'+dummy.id,null,token,'GET');
    assert.equal(prior.data.health.current-after.data.health.current,hitResult.damage+40);
    assert.equal(prior.data.health.zones.chest.current-after.data.health.zones.chest.current,hitResult.damage);
    await request(loc+'/combat/end',{},token);
    const storm=rules.find(a=>a.name==='Штормовой камень');assert.ok(storm);
    const stormTarget=await request('/lobbies/characters/'+dummy.id,null,token,'GET');
    stormTarget.data.equipment={armor:{containers:[{item:{id:'storm-artifact',category:'artifact',name:storm.name,attributes:storm}}]}};
    await request('/lobbies/characters/'+dummy.id,{data:stormTarget.data},token,'PUT');
    await request(loc+'/combat/start',{location_character_ids:[first.location_character_id,second.location_character_id],
        initiator_location_character_id:first.location_character_id,initiator_first:true},token);
    await request(loc+'/combat/action',{...command,action_key:'aim'},token);
    const stormBefore=await request('/lobbies/characters/'+dummy.id,null,token,'GET');
    const stormShot=await request(loc+'/combat/action',{...command,action_key:'attack',fire_mode:'aimed',target_zone:'chest',shot_count:1,volley_count:1,action_points:cost},token);
    const stormHit=stormShot.attack.results[0];assert.ok(stormHit.hit);
    assert.equal(stormHit.artifact_reactions.length,1);assert.equal(stormHit.artifact_reactions[0].damage_type,'electric');
    assert.equal(stormHit.artifact_reactions[0].raw_damage,50);assert.equal(stormHit.artifact_reactions[0].damage,40);
    const stormAfter=await request('/lobbies/characters/'+dummy.id,null,token,'GET');
    assert.equal(stormBefore.data.health.current-stormAfter.data.health.current,stormHit.damage+40);
    assert.equal(stormBefore.data.health.zones.chest.current-stormAfter.data.health.zones.chest.current,stormHit.damage);
    await page.until(`Number(document.querySelector('[name="health.current"]')?.value)===${stormAfter.data.health.current}`,'Электрический ответ Штормового камня обновил общий пул в листе');
    await request(loc+'/combat/end',{},token);
    const stormClock=await request(api,null,token,'GET'),stormAbsolute=stormClock.game_day*1440+stormClock.game_time_minutes+10;
    await request(api+'/time',{game_day:Math.floor(stormAbsolute/1440),game_time_minutes:stormAbsolute%1440},token,'PATCH');
    const stormPeriodic=await request('/lobbies/characters/'+dummy.id,null,token,'GET');
    assert.equal(stormAfter.data.health.current-stormPeriodic.data.health.current,50,'Периодические −50 ОЗ не вызывают электрической реакции');
    fs.writeFileSync(path.join(safeSession.artifacts,'storm-stone.json'),JSON.stringify({scene:'Припять',realShot:true,
        rawElectric:50,protectedElectric:40,poolOnly:true,networkVisible:true,periodicHealthCost:50,noPeriodicBacklash:true},null,2));
    console.log('Штормовой камень: реальный выстрел, 50 электрического/40 после защиты только в общий пул, обновление листа; −50 за10минут без ответного электричества.');
    fs.writeFileSync(path.join(safeSession.artifacts,'artifact-reactions.json'),JSON.stringify({scene:'Припять',artifact:rule.name,
        damage:55,initialSeconds:active[0].remaining_seconds,nextSeconds:reactions(next.data)[0].remaining_seconds,
        persistedAfterSave:true,expired:true,networkVisible:true,shard:{roll:50,damage:hitResult.damage,backlash:40,poolOnly:true},skin:{before:7,afterRound:5}},null,2));
    console.log('Артефакты: настоящее горение, временный штраф, сохранение, сетевое обновление и истечение по раундам проверены в Припяти.');
    console.log('Пылающий осколок: настоящее попадание, 50% на границе, 50 термического до защиты/40 после, только общий пул ОЗ.');
    await require('./daily_needs.browser.cjs')({page,request,token,characterId:actor.id,safeSession});
    await require('./artifact_devices.browser.cjs')({page,request,token,lobby,loc,characterId:actor.id,rules,safeSession});
    await require('./pda_spam.browser.cjs')({page,request,token,lobby,loc,characterId:actor.id,locationId:location.id,safeSession});
    await require('./pda_instruments.browser.cjs')({page,request,token,lobby,loc,characterId:actor.id,fixture,safeSession});
    await require('./electronics_burn.browser.cjs')({page,request,token,lobby,loc,locationId:location.id,safeSession});
    await require('./air_filler.browser.cjs')({page,request,token,lobby,loc,locationId:location.id,safeSession});
    await require('./pda_identity.browser.cjs')({page,request,token,lobby,loc,locationId:location.id,characterId:actor.id,safeSession});
};
