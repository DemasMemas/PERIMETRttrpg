// Три последовательных платных улучшения и реальная аномалия на Припяти.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,loc,locationId,safeSession})=>{
    await page.evaluate('window.closeCharacterSheet();true');
    const api='/lobbies/'+lobby.id,offered=await request(api+'/templates',null,token,'GET');
    const people=await request(api+'/characters',null,token,'GET'),previous=people.find(c=>c.name==='Получатель спам-атаки');
    await request(loc+'/spawn_character',{character_id:previous.id,tile_x:45,tile_y:48,force_relocate:true},token);
    const template=offered.global.find(t=>t.category==='armor'&&t.attributes?.modification_category==='Аномальная'&&['Н','H','3','4'].includes(String(t.item_class).toUpperCase()));assert.ok(template);
    const armor={id:'air-filler-armor',templateId:template.id,name:template.name,category:template.category,itemClass:template.item_class,
        quantity:1,attributes:structuredClone(template.attributes),stage:1,stageDurability:1000,currentStageDurability:1000,
        durability:100,maxDurability:100,modifications:[]};
    const actor=await request(api+'/characters',{name:'Проверка воздушного наполнителя',data:{equipment:{armor},
        inventory:{money:1000000,pockets:[]},health:{current:700,max:700,effects:[]}}},token);
    const npc=await request(api+'/characters',{name:'Бронник испытания наполнителя',data:{is_npc:true,inventory:{money:0}}},token);
    await request(api+'/characters/'+npc.id+'/merchant',{character_revision:npc.data._revision,enabled:false,repair_enabled:true,
        modification_enabled:true,workbenches:['Станок Бронника'],prices:{}},token,'PATCH');
    const placed=await request(loc+'/spawn_character',{character_id:actor.id,tile_x:46,tile_y:48},token);
    await request(loc+'/spawn_character',{character_id:npc.id,tile_x:46,tile_y:49},token);
    const url='/lobbies/characters/'+actor.id,get=()=>request(url,null,token,'GET');
    for(const rule of ['armor-d5','armor-d6','armor-d7']){
        const fresh=await get(),master=await request('/lobbies/characters/'+npc.id,null,token,'GET');
        await request(loc+'/professional-modification',{actor_location_character_id:placed.location_character_id,specialist_character_id:npc.id,
            target_path:['equipment','armor'],item_id:armor.id,rule_id:rule,execute:true,operation_id:'native-'+rule,
            actor_revision:fresh.data._revision,specialist_revision:master.data._revision},token);
    }
    assert.deepEqual((await get()).data.equipment.armor.modifications.map(m=>m.ruleId),['armor-d5','armor-d6','armor-d7']);
    const exposure=async()=>{
        await request(loc+'/characters/'+actor.id+'/facing',{facing_x:1,facing_y:0},token,'PATCH');
        await request(loc+'/combat/start',{location_character_ids:[placed.location_character_id],initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
        await request(loc+'/combat/action',{location_character_id:placed.location_character_id,action_key:'convert_free_action_to_movement'},token);
        await page.evaluate(`window.currentLocationCharacterId=${actor.id};window.currentCharacterId=${actor.id};window.__airMoves=[];window.socket.on('character_moved',e=>__airMoves.push(e));void window.socket.emit('move_in_location',{token:localStorage.getItem('access_token'),location_id:${locationId},character_id:${actor.id},x:47,y:48,floor_level:0,movement_mode:'walk'});true`);
        await page.until(`__airMoves.some(e=>e.character_id===${actor.id}&&e.x===47&&e.y===48)`,'Вход в Аккумулятор для наполнителя');
        const before=(await get()).data.health.current;
        await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
        const after=await get();await request(loc+'/combat/end',{},token);return {damage:before-after.data.health.current,data:after.data};
    };
    await page.evaluate(`window.openCharacterSheet(${actor.id},'basic').then(()=>true)`);
    const protectedHit=await exposure();assert.ok(protectedHit.damage>0);
    assert.ok(!protectedHit.data.health.effects.some(e=>e.type==='anomaly_electric_penalty'));
    let data=(await get()).data;data.equipment.armor.protectionDisabled=true;await request(url,{data},token,'PUT');
    await request(loc+'/spawn_character',{character_id:actor.id,tile_x:46,tile_y:48,force_relocate:true},token);
    const unprotectedHit=await exposure();assert.ok(unprotectedHit.damage>0);
    assert.ok(unprotectedHit.data.health.effects.some(e=>e.type==='anomaly_electric_penalty'));
    await page.until("document.getElementById('character-sheet-modal')?.innerText.includes('Электрошок')",'Без работающего наполнителя эффект появился в листе');
    fs.writeFileSync(path.join(safeSession.artifacts,'air-filler.json'),JSON.stringify({scene:'Припять',installedViaNpc:['armor-d5','armor-d6','armor-d7'],
        anomaly:'Аккумулятор',protectedDamage:protectedHit.damage,protectedExtraEffect:false,disabledArmorDamage:unprotectedHit.damage,disabledArmorExtraEffect:true},null,2));
    console.log('Воздушный наполнитель: 3 последовательные установки у бронника, урон проходит, электроэффект отменён; отключение брони возвращает эффект.');
};
