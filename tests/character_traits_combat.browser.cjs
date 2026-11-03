const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');

module.exports=async({page,peer,request,token,lobby,character,rules,safeSession,click})=>{
    const fixture=JSON.parse(fs.readFileSync(path.resolve(__dirname,'../tmp/pripyat-regression.json'),'utf8'));
    const location=await request(`/lobbies/${lobby.id}/locations`,{...fixture,objects:undefined,world_tile_x:8,world_tile_z:8},token);
    const url=`/lobbies/${lobby.id}/locations/${location.id}`;
    await request(url+'/objects/batch',{objects:fixture.objects},token);
    const fresh=await request(`/lobbies/characters/${character.id}`,null,token,'GET');
    const traits=['Укрощенный адреналин','Никто не должен знать о черте','Глухая оборона'].map(name=>{
        const t=rules.traits.find(t=>t.name===name);return {ruleId:t.id,name:t.name,effect:t.effect,cost:t.cost,templateId:t.templateId};
    });
    await request(`/lobbies/characters/${character.id}`,{data:{...fresh.data,features:{specialTraits:traits},equipment:{},
        weapons:[{id:'creation-pistol',name:'Проверочный пистолет',subcategory:'Пистолеты',range:20,damage:10,
            ergonomics:100,durability:100,maxDurability:100,fixedMagazine:true,ammo:10,
            fixedAmmo:[{name:'9x18',quantity:10,attributes:{damage:10}}],attributes:{noise:80,fire_rate:5,min_strength:0,magazine_size:10}}]}},token,'PUT');
    const victim=await request(`/lobbies/${lobby.id}/characters`,{name:'Цель проверки черт',data:{}},token);
    await request(url+'/spawn_character',{character_id:character.id,tile_x:41,tile_y:48},token);
    await request(url+'/spawn_character',{character_id:victim.id,tile_x:41,tile_y:50},token);
    const initial=await request(url+'/combat',null,token,'GET');
    const actorId=initial.characters.find(c=>c.character_id===character.id).location_character_id;
    const targetId=initial.characters.find(c=>c.character_id===victim.id).location_character_id;
    for(const client of [page,peer]){
        await client.evaluate(`window.closeCharacterSheet?.();(async()=>{window.L=await import('/static/js/locationScene.js');await enterLocation(${location.id})})()`);
        await client.until('window.locationControls&&document.getElementById("scene-loading-screen").classList.contains("is-hidden")','копия Припяти загружена');
        await client.evaluate(`window.openCharacterSheet(${character.id},'skills')`);
        await client.until('document.querySelector("#sheet-tab-skills [name=\\"skills.physical.agility.base\\"]")','лист в Припяти открыт');
    }
    await page.evaluate('window.closeCharacterSheet();L.requestCombatStartForAttack('+character.id+')');
    await page.until('document.querySelector(".combat-participant-clear")','выбор участников с инициатором');
    await click('.combat-participant-clear');
    assert.deepEqual(await page.evaluate('[...document.querySelectorAll(".combat-participant-list input:checked")].map(i=>Number(i.value))'),[actorId]);
    assert.equal(await page.evaluate('document.querySelector(".combat-participant-list input:checked").disabled'),true);
    fs.writeFileSync(path.join(safeSession.artifacts,'combat-participants-clear.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    await click('.combat-participant-cancel');
    if(await page.evaluate('document.querySelector(".combat-hud-collapse-btn").getAttribute("aria-expanded")==="false"'))await click('.combat-hud-collapse-btn');
    await click('.combat-start-btn');
    await page.until('document.querySelector(".combat-participant-clear")','выбор участников без инициатора');
    await click('.combat-participant-clear');
    assert.equal(await page.evaluate('document.querySelectorAll(".combat-participant-list input:checked").length'),0);
    await click('.combat-participant-cancel');
    let state=await request(url+'/combat/start',{location_character_ids:[actorId,targetId],initiator_location_character_id:actorId,initiator_first:true},token);
    state=await request(url+'/combat',null,token,'GET');
    assert.equal(state.current_character.action_points_current,6,'первый раунд: +1 ОД от черты');
    const action=payload=>request(url+'/combat/action',{location_character_id:actorId,...payload},token);
    await action({action_key:'melee_swing'});
    state=await request(url+'/combat',null,token,'GET');
    assert.equal(state.current_character.action_points_current,6,'замах бесплатный');
    await action({action_key:'melee_block',action_points:1});
    state=await request(url+'/combat',null,token,'GET');
    assert.equal(state.current_character.action_points_current,6,'блок на 1 ОД дешевле');
    assert.ok(state.current_character.melee_block_effectiveness>=4,'бонус эффективности блока');
    await action({action_key:'draw_weapon',weapon_index:0});
    const shot=await action({action_key:'attack',weapon_index:0,fire_mode:'unaimed',shot_count:1,action_points:1,target_character_id:victim.id});
    assert.equal(shot.attack.shot_count,1);
    for(const client of [page,peer])await client.until(`L.getCombatState()?.characters?.find(c=>c.character_id===${character.id})?.effects?.some(e=>e.type==='deafness'&&e.value===40)`,'шум настоящего выстрела у обоих клиентов');
    for(let round=1;round<=3;round++){
        await request(url+'/combat/end_turn',{location_character_id:actorId},token);
        await request(url+'/combat/end_turn',{location_character_id:targetId},token);
    }
    state=await request(url+'/combat',null,token,'GET');
    assert.equal(state.round_number,4);assert.equal(state.current_character.action_points_current,5,'с четвёртого раунда бонус ОД закончился');
    await request(url+'/combat/end',{},token);
    await require('./character_traits_runtime.browser.cjs')({page,peer,request,token,lobby,location,url,rules,safeSession,click,partnerCharacter:character});
    fs.writeFileSync(path.join(safeSession.artifacts,'character-traits-combat.json'),JSON.stringify({scene:'копия Припяти',bulkInitiativeClear:true,initiatorPreserved:true,firstRoundAp:6,freeSwing:true,discountedBlock:true,liveShotBothClients:true,roundFourAp:5},null,2));
};
