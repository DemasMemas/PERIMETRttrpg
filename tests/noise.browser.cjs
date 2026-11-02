// Выстрелы, слух и раундовый спад у двух клиентов на копии Припяти.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,peer,request,token,lobby,url,actor,victim,actorId,victimId,safeSession,enter})=>{
    await request(url+'/combat/end',{},token);
    for(const character of [actor,victim]){
        const old=await request(`/lobbies/characters/${character.id}`,null,token,'GET');
        await request(`/lobbies/characters/${character.id}`,{data:{...old.data,equipment:{},
            health:{current:700,max:700,painLevel:0,stress:0,effects:[]},
            ...(character.id===actor.id?{weapons:[{id:'noise-browser-gun',name:'Проверочный пистолет',subcategory:'Пистолеты',
                range:20,damage:10,ergonomics:100,durability:100,maxDurability:100,fixedMagazine:true,ammo:10,
                fixedAmmo:[{name:'9x18',quantity:10,attributes:{damage:10}}],attributes:{noise:80,fire_rate:5,min_strength:0,magazine_size:10}}]}:{})}},token,'PUT');
    }
    await request(url+'/combat/start',{location_character_ids:[actorId,victimId],initiator_location_character_id:actorId,initiator_first:true},token);
    for(const client of [page,peer])await client.evaluate(`(async()=>{await window.openCharacterSheet(${actor.id},'skills')})()`);
    await request(url+'/combat/action',{location_character_id:actorId,action_key:'draw_weapon',weapon_index:0},token);
    const shoot=()=>request(url+'/combat/action',{location_character_id:actorId,action_key:'attack',weapon_index:0,
        fire_mode:'unaimed',shot_count:1,action_points:1,target_character_id:victim.id},token);
    const first=await shoot();assert.equal(first.attack.shot_count,1);
    assert.equal(first.attack.noise_targets.find(t=>t.character_id===actor.id).noise_received,40);
    for(const client of [page,peer])await client.until(`L.getCombatState()?.characters?.find(c=>c.character_id===${actor.id})?.effects?.some(e=>e.type==='deafness'&&e.value===40)`,'шум выстрела у обоих клиентов');
    await shoot();
    for(const client of [page,peer])await client.until(`grenadeSheets.some(e=>e.character_id===${actor.id}&&e.updates?.data?.health?.effects?.some(f=>f.type==='deafness'&&f.value===80))`,'лист получил накопленную глухоту');
    const roll=await request(url+'/combat/action',{location_character_id:actorId,action_key:'narrative_action',action_points:0,
        narrative_action_name:'Слух после стрельбы',narrative_roll_required:true,narrative_skill_path:'skills.physical.awareness.hearing',narrative_difficulty:8},token);
    assert.equal(roll.narrative_action.check.hearing_modifier,-5);
    assert.equal(await page.evaluate(`import('/static/js/hearingRules.js').then(m=>m.hearingModifier(80))`),-5);
    await request(url+'/combat/end_turn',{location_character_id:actorId},token);
    const mid=await request(`/lobbies/characters/${actor.id}`,null,token,'GET');
    assert.ok(mid.data.health.effects.some(e=>e.type==='deafness'&&e.value===80),'в конце личного хода глухота не спала');
    await request(url+'/combat/end_turn',{location_character_id:victimId},token);
    for(const client of [page,peer])await client.until(`L.getCombatState()?.characters?.find(c=>c.character_id===${actor.id})?.effects?.some(e=>e.type==='deafness'&&e.value===60)`,'в конце раунда спад ровно на двадцать');
    await peer.call('Page.reload');
    for(let attempt=0;;attempt++)try{await enter(peer);break;}catch(e){if(attempt>=3||!/navigated or closed|context was destroyed|Cannot find context/.test(e.message))throw e;}
    const saved=await request(`/lobbies/characters/${actor.id}`,null,token,'GET');
    assert.ok(saved.data.health.effects.some(e=>e.type==='deafness'&&e.value===60));
    assert.equal(saved.data.weapons[0].ammo,8);
    fs.writeFileSync(path.join(safeSession.artifacts,'noise.json'),JSON.stringify({scene:'копия Припяти',twoClients:true,
        shotNoise:80,outdoorReceivedPerShot:40,accumulated:80,hearingPenalty:-5,afterRound:60,reloadDidNotRepeatNoise:true,ammoRemaining:8},null,2));
    console.log('Шум: два выстрела, два клиента, штраф слуха, спад по раунду и reload — проверено.');
};
