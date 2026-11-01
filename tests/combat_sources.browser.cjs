const assert=require('node:assert/strict');

module.exports=async({page,peer,request,token,lobby,url,actor,actorId,npc,npcId,player,setActor})=>{
    const sheetUrl=`/lobbies/characters/${actor.id}`;
    const start=ids=>request(url+'/combat/start',{location_character_ids:ids,
        initiator_location_character_id:ids[0],initiator_first:true},token);
    const end=id=>request(url+'/combat/end_turn',{location_character_id:id},token);
    const act=(id,payload)=>request(url+'/combat/action',{location_character_id:id,...payload},token);
    const place=(id,x,y,force=true)=>request(url+'/spawn_character',{character_id:id,tile_x:x,tile_y:y,
        floor_level:0,...(force?{force_relocate:true}:{})},token);
    await setActor(false,false,0);
    await place(actor.id,41,48);
    const anomaly=await request(url+'/objects',{type:'anomaly',name:'Проверочный Морозильник',
        tile_x:42,tile_y:48,properties:{anomaly_key:'morozilnik',level:0}},token);
    const anomalyStart=await start([actorId,npcId]);
    if(anomalyStart.current_character.posture!=='standing') await act(actorId,{action_key:'change_posture',posture:'standing',payment:'action'});
    await act(actorId,{action_key:'convert_free_action_to_movement'});
    await peer.evaluate(`void socket.emit('move_in_location',{token:localStorage.getItem('access_token'),
        location_id:${url.split('/').pop()},character_id:${actor.id},x:42,y:48,movement_mode:'walk'})`);
    for(const client of [page,peer]) await client.until(`window.locationCombatState?.characters.some(c=>c.character_id===${actor.id}&&c.active_anomaly?.key==='morozilnik')`,'настоящий вход в аномалию');
    await end(actorId);
    for(const client of [page,peer]) await client.until('psiSheets.some(s=>s.health.current===550&&s.health.painLevel===4)','урон 150 и Боль 4 у двух клиентов');
    const anomalyHealth=(await request(sheetUrl,null,token,'GET')).data.health;
    assert.equal(anomalyHealth.current,550);assert.equal(anomalyHealth.painLevel,4);
    await request(url+'/combat/end',{},token);
    assert.equal(await page.evaluate(`(async()=>{const r=await fetch('/lobbies/${lobby.id}/locations/objects/${anomaly.id}',
        {method:'DELETE',headers:{Authorization:'Bearer '+localStorage.getItem('access_token')}});return r.status})()`),204);

    await setActor(false,false,0);
    await place(actor.id,41,48);
    const psion=await request(`/lobbies/${lobby.id}/mutants`,{mutant_type:'Псионик',name:'Псионик: три удара'},token);
    await place(psion.id,43,48,false);
    await request(url+`/characters/${psion.id}/facing`,{facing_x:-1,facing_y:0},token,'PATCH');
    const psionId=(await request(url+'/combat',null,token,'GET')).characters.find(c=>c.character_id===psion.id).location_character_id;
    await start([psionId,actorId]);
    const before=(await request(sheetUrl,null,token,'GET')).data.health.psyState;
    assert.equal(before,0);
    // ГМ добавляет 4 ОД: три каталожных Пси-удара стоят по 3 ОД.
    await request(url+'/combat/adjust',{location_character_id:psionId,action_points:4},token);
    const strikes=[];
    for(const expected of [20,40,60]){
        await act(psionId,{action_key:'mutant_psy_attack',weapon_index:0,target_character_id:actor.id});
        const health=(await request(sheetUrl,null,token,'GET')).data.health;
        assert.equal(health.psyState,expected);assert.equal(health.current,700);
        assert.ok(!health.combatMeta?.lastPsiTurnStart);
        strikes.push(expected);
    }
    const ownStart=await end(psionId);
    const ownHealth=(await request(sheetUrl,null,token,'GET')).data.health;
    assert.equal(ownHealth.psyState,60);
    // Провал спасброска Пси-удара вправе выбить оружие: тогда атаки по себе нет.
    assert.ok([680,700].includes(ownHealth.current));
    const ownEnd=await end(actorId);
    const field=ownEnd.psy_end_turn.psy_fields.find(item=>item.source_character_id===psion.id);
    assert.ok(field,'каталожное Пси-Поле Псионика также проверяется в конце хода');
    const fieldGain=field.save.success?0:5;
    assert.equal(field.psy_state?.net_change??0,fieldGain);
    const resetDamage=(60+fieldGain)*10;
    assert.equal(ownEnd.psy_crisis.psy_state,60+fieldGain);
    assert.equal(ownEnd.psy_crisis.reset_damage,resetDamage);
    const expectedHp=ownHealth.current-resetDamage;
    for(const client of [page,peer]) await client.until(`psiSheets.some(s=>s.health.current===${expectedHp}&&s.health.psyState===0&&s.health.painLevel===10)`,'три Пси-удара: сброс только в конце своего хода и Боль у двух клиентов');
    await request(url+'/combat/end',{},token);
    // Оплаченный снимок запоздал в сеть уже после завершения действия.
    // Запоздавшая строка должна исчезнуть автоматически, без нажатия кнопок.
    await setActor(false,false,0);
    await place(actor.id,41,48);
    await request(url+`/characters/${actor.id}/facing`,{facing_x:1,facing_y:0},token,'PATCH');
    await start([actorId,npcId]);
    await act(actorId,{action_key:'draw_weapon',weapon_index:0});
    await act(actorId,{action_key:'aim',weapon_index:0,target_character_id:npc.id});
    const afterAim=await request(url+'/combat',null,token,'GET');
    await request(url+'/combat/adjust',{location_character_id:actorId,
        action_points:-afterAim.current_character.action_points_current},token);
    const pendingId='gm-remove-paid-row';
    const pending=await act(actorId,{action_key:'aim',weapon_index:0,target_character_id:npc.id,pending_action_id:pendingId});
    assert.equal(pending.pending_action,true);
    const ready=await end(npcId);
    assert.equal(ready.current_character.deferred_action.status,'ready');
    for(const client of [page,peer]) await client.until(`window.locationCombatState?.current_character?.character_id===${actor.id}&&window.locationCombatState.current_character.aim_accuracy_bonus===2&&!window.locationCombatState.current_character.deferred_action`,'автоматическое завершение оплаченного действия');
    const completed=await request(url+'/combat',null,token,'GET');
    assert.ok(!completed.current_character.deferred_action);
    assert.equal(completed.current_character.aim_accuracy_bonus,2);
    await page.evaluate(`L.setCombatState(${JSON.stringify(ready)})`);
    for(const client of [page,peer]) await client.until(`!window.locationCombatState?.characters.some(c=>c.deferred_action?.id==='${pendingId}')&&!document.querySelector('.combat-deferred-retry')`,'оплаченная строка исчезла сама у ГМа и игрока');
    const removed=await request(url+'/combat',null,token,'GET');
    assert.equal(removed.current_character.action_points_current,completed.current_character.action_points_current);
    assert.equal(removed.current_character.aim_accuracy_bonus,2);
    assert.ok(!removed.current_character.deferred_action);
    // Подготовка ready-снимка без преждевременного исполнения в тестовых вкладках.
    // Серверные роли и токены не меняются; перед проверкой возвращаем обычный вид.
    for(const client of [page,peer]) await client.evaluate(`window.__resumeRole={gm:window.isGM,id:localStorage.getItem('user_id')};window.isGM=false;localStorage.setItem('user_id','-1')`);
    const failedId='auto-cancel-missing-target';
    await request(url+'/combat/adjust',{location_character_id:actorId,
        action_points:-removed.current_character.action_points_current},token);
    assert.equal((await act(actorId,{action_key:'aim',weapon_index:0,target_character_id:npc.id,pending_action_id:failedId})).pending_action,true);
    const failedReady=await end(npcId);
    assert.equal(failedReady.current_character.deferred_action.status,'ready');
    await request(url+`/characters/${npc.id}`,null,token,'DELETE');
    const paidAp=failedReady.current_character.action_points_current;
    for(const client of [page,peer]) await client.evaluate(`window.isGM=__resumeRole.gm;localStorage.setItem('user_id',__resumeRole.id);L.setCombatState(${JSON.stringify(failedReady)})`);
    for(const client of [page,peer]) await client.until(`!window.locationCombatState?.characters.some(c=>c.deferred_action?.id==='${failedId}')&&!document.querySelector('.combat-deferred-cancel[data-action="${failedId}"]')&&!document.querySelector('.combat-deferred-retry')`,'неудавшееся действие автоматически отменено у обоих клиентов');
    const failedState=await request(url+'/combat',null,token,'GET');
    assert.equal(failedState.current_character.action_points_current,paidAp);
    assert.equal(failedState.current_character.aim_accuracy_bonus,2);
    const late=await page.evaluate(`(async()=>{const r=await fetch('${url}/combat/action',{
        method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+localStorage.getItem('access_token')},
        body:JSON.stringify({location_character_id:${actorId},action_key:'aim',resume_pending_action_id:'${failedId}'})});return r.status})()`);
    assert.equal(late,409,'неудавшееся действие нельзя завершить позднее');
    await request(url+'/combat/end',{},token);
    console.log(`PASS: аномалия 150/Боль 4, три реальных Пси-удара, Пси-Поле +${fieldGain}, сброс ${resetDamage}/Боль 10, автоматическое исчезновение оплаченной строки и отмена неудавшегося действия у двух клиентов`);
    return {anomaly:{damage:150,pain:anomalyHealth.painLevel},strikes,
        ownStart:ownStart.psy_turn_start,field,reset:ownEnd.psy_crisis,remainingHp:expectedHp,
        removedPaidRow:{id:pendingId,automatic:true,ap:removed.current_character.action_points_current,aimBonus:2},
        failedAction:{id:failedId,automatic:true,ap:paidAp,lateResume:late},twoClients:true};
};
