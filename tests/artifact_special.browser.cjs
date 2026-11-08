// Подготовленное время ношения, последний реальный раунд и две активации через обычные кнопки.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,loc,locationId,rules,safeSession})=>{
    assert.ok(safeSession.verified);assert.equal(process.env.VTT_SAFE_SCENARIO,'artifacts');
    await page.evaluate("document.querySelector('[data-pda-close]')?.click();window.closeCharacterSheet();true");
    await page.evaluate(`(()=>{
        const original=window.fetch;window.__artifactHttp=[];
        window.fetch=async(...args)=>{const response=await original(...args);
            if(String(args[0]).includes('/combat/action')&&String(args[1]?.body||'').includes('artifact_special')){
                const body=await response.clone().json();window.__artifactHttp.push({status:response.status,error:body.error||body.message||null});
            }return response;};
        const activate=window.activateArtifactSpecial;
        window.activateArtifactSpecial=(...args)=>(window.__artifactUsePromise=activate(...args));
        return true;
    })()`);
    const rule=rules.find(row=>row.name==='Ядро твари');assert.ok(rule);
    const character=await request('/lobbies/'+lobby.id+'/characters',{name:'Проверка особого свойства',data:{
        skills:{other:{survival:{base:15}}},equipment:{armor:{id:'special-test-armor',name:'Броня для проверки',
            containers:[{item:{id:'native-special-artifact',name:rule.name,category:'artifact',quantity:1,attributes:rule}}]}},
        health:{current:100,max:700,radiation:10,effects:[]}}},token);
    const url='/lobbies/characters/'+character.id,get=()=>request(url,null,token,'GET');
    const placed=await request(loc+'/spawn_character',{character_id:character.id,tile_x:42,tile_y:46},token);
    await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:character.id},token);
    const open=async()=>{
        await page.evaluate(`window.openCharacterSheet(${character.id},'equipment').then(()=>true)`);
        await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Вкладка брони доступна');
        await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
        await page.until("document.querySelector('[data-artifact-special=\"native-special-artifact\"]')",'Кнопка особого свойства в экипировке');
    };
    await open();assert.equal((await get()).data._artifact_specials[0].worn_seconds,86394);
    assert.ok(await page.evaluate("document.querySelector('[data-artifact-special=\"native-special-artifact\"]').disabled"));
    await request(loc+'/combat/start',{location_character_ids:[placed.location_character_id],
        initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
    await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
    const ready=await get();assert.equal(ready.data._artifact_specials[0].worn_seconds,86400);
    await page.until("!document.querySelector('[data-artifact-special=\"native-special-artifact\"]').disabled",'Настоящий раунд завершил накопление времени');
    const before=(await request(loc+'/combat',null,token,'GET')).current_character.action_points_current;
    const use=()=>page.evaluate("document.querySelector('[data-artifact-special=\"native-special-artifact\"]').click();true");
    await use();
    const waitUsed=async count=>{for(let i=0;i<70;i++){const data=await get();if(data.data._artifact_specials[0].remaining_uses===2-count)return data;await new Promise(r=>setTimeout(r,100));}assert.fail('Особое свойство не применилось');};
    const first=await waitUsed(1);assert.equal(first.data.health.radiation,0);assert.equal(first.data.health.current,200);
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    assert.deepEqual(await page.evaluate('window.__artifactHttp.map(r=>r.status)'),[200]);
    assert.equal((await request(loc+'/combat',null,token,'GET')).current_character.action_points_current,before-1);
    assert.equal(first.data._artifact_specials[0].cooldown_seconds,86400);
    await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
    let second=await get();second.data.health.radiation=10;
    await request(url,{data:second.data},token,'PUT');
    await open();await page.until("!document.querySelector('[data-artifact-special=\"native-special-artifact\"]').disabled",'Осталось второе использование');
    await use();second=await waitUsed(2);
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    assert.deepEqual(await page.evaluate('window.__artifactHttp.map(r=>r.status)'),[200,200]);
    assert.equal(second.data.health.current,300);assert.equal(second.data.health.radiation,0);
    assert.equal(second.data._artifact_specials[0].cooldown_seconds,86394,'Второе применение не сдвигает общий срок');
    await open();
    await page.until("document.querySelector('[data-artifact-special=\"native-special-artifact\"]').disabled",'Перезарядка показана после переоткрытия');
    fs.writeFileSync(path.join(safeSession.artifacts,'artifact-special.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    await request(loc+'/combat/end',{},token);
    const generator=rules.find(row=>row.name==='Генератор'),generatorData=await get();assert.ok(generator);
    generatorData.data.equipment.armor.containers=[{item:{id:'native-special-artifact',name:generator.name,category:'artifact',quantity:1,attributes:generator}}];
    // Новый физический экземпляр получает новый ID, готовность предыдущего не наследуется.
    generatorData.data.equipment.armor.containers[0].item.id='native-generator';
    await request(url,{data:generatorData.data},token,'PUT');
    await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:character.id},token);
    await request(loc+'/combat/start',{location_character_ids:[placed.location_character_id],
        initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
    await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
    const generatorBefore=await get();assert.equal(generatorBefore.data._artifact_profile.movement_penalty,-1);
    await page.evaluate(`window.openCharacterSheet(${character.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Броня после замены артефакта');
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
    await page.until("document.querySelector('[data-artifact-special=\"native-generator\"]')&&!document.querySelector('[data-artifact-special=\"native-generator\"]').disabled",'Генератор доступен без радиации');
    await page.evaluate("document.querySelector('[data-artifact-special=\"native-generator\"]').click();true");
    let generatorActive;
    for(let i=0;i<70;i++){generatorActive=await get();if(generatorActive.data._artifact_profile.movement_penalty===-3)break;await new Promise(r=>setTimeout(r,100));}
    assert.equal(generatorActive.data._artifact_profile.movement_penalty,-3);
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    const actionResponses=await page.evaluate('window.__artifactHttp');
    assert.deepEqual(actionResponses.map(row=>row.status),[200,200,200],JSON.stringify(actionResponses));
    const special=d=>d.health.effects.find(effect=>effect.source?.startsWith('artifact-special:'));
    assert.equal(special(generatorActive.data).remaining_seconds,600);
    await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
    assert.equal(special((await get()).data).remaining_seconds,594);
    await request(loc+'/combat/end',{},token);
    const clock=await request('/lobbies/'+lobby.id,null,token,'GET'),absolute=clock.game_day*1440+clock.game_time_minutes+10;
    await request('/lobbies/'+lobby.id+'/time',{game_day:Math.floor(absolute/1440),game_time_minutes:absolute%1440},token,'PATCH');
    const generatorExpired=await get();assert.equal(generatorExpired.data._artifact_profile.movement_penalty,-1);
    assert.ok(!special(generatorExpired.data));
    fs.writeFileSync(path.join(safeSession.artifacts,'artifact-special.json'),JSON.stringify({scene:'Припять',artifact:rule.name,
        preparedWornSeconds:86394,realRoundSeconds:6,readyAfterRealRound:true,realButtonActivations:2,apPerUse:1,
        first:{radiation:0,hp:200,cooldown:86400},second:{radiation:0,hp:300,cooldown:86394},reopened:true,
        actionResponses,generator:{buttonActivated:true,movementBefore:-1,movementActive:-3,initialSeconds:600,afterRound:594,expired:true}},null,2));
    console.log('Особые свойства: подготовленное ношение+реальный раунд, две кнопки Ядра твари по1ОД без рук, радиация→ОЗ; общий срок после первого, переоткрытие.');
    console.log('Генератор: кнопка без радиации, фактический штраф перемещения−1→−3,600→594секунд за раунд; истечение по игровому времени возвращает−1.');
};
