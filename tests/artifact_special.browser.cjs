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
    const heat=rules.find(row=>row.name==='Сердце жара'),heatData=await get();assert.ok(heat);
    heatData.data.equipment.armor.containers=[{item:{id:'native-heat-heart',name:heat.name,category:'artifact',quantity:1,attributes:heat}}];
    await request(url,{data:heatData.data},token,'PUT');
    await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:character.id},token);
    await request(loc+'/combat/start',{location_character_ids:[placed.location_character_id],
        initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
    await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
    const burning=await get();burning.data.health.effects.push({type:'burning',name:'Горение',remaining:3,
        tick:'round_end',source:'native-heat-immunity',damage_per_round:50});
    await request(url,{data:burning.data},token,'PUT');
    await page.evaluate(`window.openCharacterSheet(${character.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Броня с Сердцем жара');
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
    await page.until("document.querySelector('[data-artifact-special=\"native-heat-heart\"]')&&!document.querySelector('[data-artifact-special=\"native-heat-heart\"]').disabled",'Сердце жара готово');
    const heatBefore=(await get()).data.health.current;
    await page.evaluate("document.querySelector('[data-artifact-special=\"native-heat-heart\"]').click();true");
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    const heatResponses=await page.evaluate('window.__artifactHttp');
    assert.deepEqual(heatResponses.map(row=>row.status),[200,200,200,200],JSON.stringify(heatResponses));
    actionResponses.push(...heatResponses.slice(3));
    await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
    const immuneRound=await get();assert.equal(immuneRound.data.health.current,heatBefore,'Первый раунд горения не повредил носителя');
    await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
    const expiredHeat=await get();assert.equal(expiredHeat.data.health.current,heatBefore-50,'После истечения вернулся обычный урон горения');
    await request(loc+'/combat/end',{},token);
    const vein=rules.find(row=>row.name==='Огненная вена'),veinData=await get();assert.ok(vein);
    veinData.data.equipment.armor.containers=[{item:{id:'native-fiery-vein',name:vein.name,category:'artifact',quantity:1,attributes:vein}}];
    veinData.data.health.effects=veinData.data.health.effects.filter(effect=>effect.type!=='burning');
    await request(url,{data:veinData.data},token,'PUT');
    await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:character.id},token);
    await request(loc+'/combat/start',{location_character_ids:[placed.location_character_id],
        initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
    await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
    await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:character.id,breathlessness:true},token);
    assert.equal((await request(loc+'/combat',null,token,'GET')).current_character.strenuous_movement_blocked_until_round,1000);
    await page.evaluate(`window.openCharacterSheet(${character.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Броня с Огненной веной');
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
    await page.until("document.querySelector('[data-artifact-special=\"native-fiery-vein\"]')&&!document.querySelector('[data-artifact-special=\"native-fiery-vein\"]').disabled",'Огненная вена готова');
    await page.evaluate("document.querySelector('[data-artifact-special=\"native-fiery-vein\"]').click();true");
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    const veinResponses=await page.evaluate('window.__artifactHttp');
    assert.deepEqual(veinResponses.map(row=>row.status),[200,200,200,200,200],JSON.stringify(veinResponses));
    actionResponses.push(...veinResponses.slice(4));
    assert.equal((await request(loc+'/combat',null,token,'GET')).current_character.strenuous_movement_blocked_until_round,0);
    const breathEffect=d=>d.health.effects.find(effect=>effect.artifact_stat==='breathless_immunity');
    assert.equal(breathEffect((await get()).data).remaining_seconds,18);
    for(let i=0;i<3;i++)await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
    assert.ok(!breathEffect((await get()).data));
    await request(loc+'/combat/end',{},token);
    const stormHeart=rules.find(row=>row.name==='Сердце грозы'),stormData=await get();assert.ok(stormHeart);
    stormData.data.equipment.armor.containers=[{item:{id:'native-storm-heart',name:stormHeart.name,category:'artifact',quantity:1,attributes:stormHeart}}];
    stormData.data.skills.physical={strength:{base:10}};
    await request(url,{data:stormData.data},token,'PUT');
    await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:character.id},token);
    await request(loc+'/combat/start',{location_character_ids:[placed.location_character_id],
        initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
    await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
    await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:character.id,breathlessness:true},token);
    assert.equal((await request(loc+'/combat',null,token,'GET')).current_character.strenuous_movement_blocked_until_round,1000);
    await page.evaluate(`window.openCharacterSheet(${character.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Броня с Сердцем грозы');
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
    await page.until("document.querySelector('[data-artifact-special=\"native-storm-heart\"]')&&!document.querySelector('[data-artifact-special=\"native-storm-heart\"]').disabled",'Сердце грозы готово');
    await page.evaluate("document.querySelector('[data-artifact-special=\"native-storm-heart\"]').click();true");
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    const stormResponses=await page.evaluate('window.__artifactHttp');
    assert.deepEqual(stormResponses.map(row=>row.status),[200,200,200,200,200,200],JSON.stringify(stormResponses));
    actionResponses.push(...stormResponses.slice(5));
    assert.equal((await request(loc+'/combat',null,token,'GET')).current_character.strenuous_movement_blocked_until_round,0);
    const stormActive=await get();assert.equal(stormActive.data._artifact_profile.movement_penalty,-20);
    assert.equal(stormActive.data._artifact_profile.skill_strength,5);
    await request(loc+'/combat/end',{},token);
    const changeMinutes=async minutes=>{const now=await request('/lobbies/'+lobby.id,null,token,'GET'),at=now.game_day*1440+now.game_time_minutes+minutes;
        await request('/lobbies/'+lobby.id+'/time',{game_day:Math.floor(at/1440),game_time_minutes:at%1440},token,'PATCH');};
    await changeMinutes(1);
    const sleeping=await get(),sleep=sleeping.data.health.effects.filter(effect=>effect.type==='sleep');
    assert.equal(sleep.length,1);assert.equal(sleep[0].remaining_seconds,552,'12секусиления+48секужепрошедшегосна');
    assert.equal(sleeping.data._artifact_profile.movement_penalty,0);
    assert.equal(sleeping.data._artifact_profile.skill_strength,0);
    await changeMinutes(10);assert.ok(!(await get()).data.health.effects.some(effect=>effect.type==='sleep'));
    const shell=rules.find(row=>row.name==='Панцирь'),shellData=await get();assert.ok(shell);
    Object.assign(shellData.data.equipment.armor,{name:'Проверочная броня',category:'armor',stage:1,
        stageDurability:1000,currentStageDurability:1000,protection:{thermal:100},attributes:{},
        containers:[{item:{id:'native-shell',name:shell.name,category:'artifact',quantity:1,attributes:shell}}]});
    await request(url,{data:shellData.data},token,'PUT');
    await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:character.id},token);
    await request(loc+'/combat/start',{location_character_ids:[placed.location_character_id],
        initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
    await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
    const shellBurning=await get();shellBurning.data.health.effects.push({type:'burning',name:'Горение',remaining:12,
        tick:'round_end',source:'native-shell-burning',damage_per_round:50});
    await request(url,{data:shellBurning.data},token,'PUT');
    await page.evaluate(`window.openCharacterSheet(${character.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Броня с Панцирем');
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
    await page.until("document.querySelector('[data-artifact-special=\"native-shell\"]')&&!document.querySelector('[data-artifact-special=\"native-shell\"]').disabled",'Панцирь готов');
    await page.evaluate("document.querySelector('[data-artifact-special=\"native-shell\"]').click();true");
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    const shellResponses=await page.evaluate('window.__artifactHttp');
    assert.deepEqual(shellResponses.map(row=>row.status),[200,200,200,200,200,200,200],JSON.stringify(shellResponses));
    actionResponses.push(...shellResponses.slice(6));
    assert.equal((await get()).data.health.effects.find(e=>e.artifact_stat==='armor_wear_immunity').remaining_seconds,60);
    for(let round=0;round<10;round++)await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
    const shellExpired=await get();assert.equal(shellExpired.data.equipment.armor.currentStageDurability,1000);
    assert.ok(!shellExpired.data.health.effects.some(e=>e.artifact_stat==='armor_wear_immunity'));
    await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
    assert.equal((await get()).data.equipment.armor.currentStageDurability,950,'После минуты снова действует износ от горения');
    await request(loc+'/combat/end',{},token);
    const psion=await request('/lobbies/'+lobby.id+'/mutants',{mutant_type:'Псионик',name:'Проверка пси-иммунитета'},token);
    const psionPlaced=await request(loc+'/spawn_character',{character_id:psion.id,tile_x:43,tile_y:46},token);
    await request(loc+'/characters/'+psion.id+'/facing',{facing_x:-1,facing_y:0},token,'PATCH');
    const psiResults=[];
    for(const [name,seconds,identity] of [['Ментальный бастион',30,'native-mental-bastion'],['Щит сознания',60,'native-mind-shield']]){
        const artifact=rules.find(row=>row.name===name),fresh=await get();assert.ok(artifact);
        fresh.data.equipment.armor.containers=[{item:{id:identity,name,category:'artifact',quantity:1,attributes:artifact}}];
        fresh.data.health.effects=fresh.data.health.effects.filter(effect=>effect.type!=='burning');
        await request(url,{data:fresh.data},token,'PUT');
        await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:character.id},token);
        await request(loc+'/combat/start',{location_character_ids:[placed.location_character_id],
            initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
        await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
        await request(loc+'/combat/end',{},token);
        await request(loc+'/combat/start',{location_character_ids:[placed.location_character_id,psionPlaced.location_character_id],
            initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
        await page.evaluate(`window.openCharacterSheet(${character.id},'equipment').then(()=>true)`);
        await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Броня с пси-защитой');
        await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
        await page.until(`document.querySelector('[data-artifact-special="${identity}"]')&&!document.querySelector('[data-artifact-special="${identity}"]').disabled`,name+' готов');
        await page.evaluate(`document.querySelector('[data-artifact-special="${identity}"]').click();true`);
        await page.evaluate('window.__artifactUsePromise.then(()=>true)');
        assert.equal((await get()).data.health.effects.find(e=>e.artifact_stat==='directed_psi_immunity').remaining_seconds,seconds);
        let stressBlocked=null;
        if(name==='Щит сознания'){
            const stressBefore=(await get()).data.health.stress;
            const stress=await request('/lobbies/'+lobby.id+'/characters/'+character.id+'/stress',{amount:1},token);
            assert.equal(stress.stress.blocked,true);assert.equal(stress.data.health.stress,stressBefore);stressBlocked=true;
        }
        const psionTurn=await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
        const psiBefore=(await get()).data.health.psyState??0,apBefore=psionTurn.current_character.action_points_current;
        const strike=await request(loc+'/combat/action',{location_character_id:psionPlaced.location_character_id,
            action_key:'mutant_psy_attack',weapon_index:0,target_character_id:character.id},token);
        assert.equal(strike.mutant_action.artifact_immune,true);assert.equal(strike.mutant_action.save,null);
        assert.equal((await get()).data.health.psyState,psiBefore);
        assert.equal((await request(loc+'/combat',null,token,'GET')).current_character.action_points_current,apBefore-3);
        await request(loc+'/combat/end',{},token);await changeMinutes(1);
        assert.ok(!(await get()).data.health.effects.some(e=>e.artifact_stat==='directed_psi_immunity'));
        if(name==='Щит сознания'){
            const stressBefore=(await get()).data.health.stress;
            const stress=await request('/lobbies/'+lobby.id+'/characters/'+character.id+'/stress',{amount:1},token);
            assert.equal(stress.data.health.stress,stressBefore+1);
        }
        psiResults.push({name,seconds,realButton:true,realPsionStrikeBlocked:true,attackerActionPoints:3,stressBlocked,expired:true});
    }
    const psiResponses=await page.evaluate('window.__artifactHttp');
    assert.deepEqual(psiResponses.map(row=>row.status),Array(9).fill(200),JSON.stringify(psiResponses));
    actionResponses.push(...psiResponses.slice(7));
    const gift=rules.find(row=>row.name==='Дар пламени'),giftData=await get();assert.ok(gift);
    giftData.data.equipment.armor.containers=[{item:{id:'native-fire-gift',name:gift.name,category:'artifact',quantity:1,attributes:gift}}];
    giftData.data.health.current=700;
    await request(url,{data:giftData.data},token,'PUT');
    await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:character.id},token);
    await request(loc+'/combat/start',{location_character_ids:[placed.location_character_id],
        initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
    await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
    await page.evaluate(`window.openCharacterSheet(${character.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Броня с Даром пламени');
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
    await page.until("document.querySelector('[data-artifact-special=\"native-fire-gift\"]')&&!document.querySelector('[data-artifact-special=\"native-fire-gift\"]').disabled",'Дар пламени готов');
    await page.evaluate("document.querySelector('[data-artifact-special=\"native-fire-gift\"]').click();true");
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    assert.equal((await get()).data._artifact_profile.protection.physical,30);
    for(let round=0;round<2;round++)await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
    const giftSecondRound=await get();assert.equal(giftSecondRound.data.health.current,700);
    assert.equal(giftSecondRound.data._artifact_profile.protection.physical,30);
    await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
    const giftExpired=await get();assert.equal(giftExpired.data.health.current,450);
    assert.equal(giftExpired.data._artifact_profile.protection.physical,0);
    await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
    assert.equal((await get()).data.health.current,450,'Цена не применяется повторно');
    await request(loc+'/combat/end',{},token);
    const giftResponses=await page.evaluate('window.__artifactHttp');
    assert.deepEqual(giftResponses.map(row=>row.status),Array(10).fill(200),JSON.stringify(giftResponses));
    actionResponses.push(...giftResponses.slice(9));
    const shield=rules.find(row=>row.name==='Живой щит'),shieldData=await get();assert.ok(shield);
    Object.assign(shieldData.data.equipment.armor,{stage:2,stageDurability:1000,currentStageDurability:800,
        protection:{thermal:100},containers:[{item:{id:'native-live-shield',name:shield.name,category:'artifact',quantity:1,attributes:shield}}]});
    shieldData.data.health.current=700;
    shieldData.data.health.effects=shieldData.data.health.effects.filter(effect=>effect.type!=='burning');
    await request(url,{data:shieldData.data},token,'PUT');
    await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:character.id},token);
    await request(loc+'/combat/start',{location_character_ids:[placed.location_character_id],
        initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
    const endShieldRound=()=>request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
    await endShieldRound();
    const shieldBurning=await get();shieldBurning.data.health.effects.push({type:'burning',name:'Горение',remaining:12,
        tick:'round_end',source:'native-shield-burning',damage_per_round:50});
    await request(url,{data:shieldBurning.data},token,'PUT');
    await page.evaluate(`window.openCharacterSheet(${character.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Броня с Живым щитом');
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
    await page.until("document.querySelector('[data-artifact-special=\"native-live-shield\"]:not(:disabled)')",'Живой щит готов');
    await page.evaluate("document.querySelector('[data-artifact-special=\"native-live-shield\"]').click();true");
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    const shieldResponses=await page.evaluate('window.__artifactHttp');
    assert.deepEqual(shieldResponses.map(row=>row.status),Array(11).fill(200),JSON.stringify(shieldResponses));
    actionResponses.push(...shieldResponses.slice(10));
    assert.equal((await get()).data.health.effects.find(effect=>effect.artifact_stat==='armor_restore_on_hit').remaining_seconds,60);
    for(const expected of [850,900]){
        await endShieldRound();
        const armor=(await get()).data.equipment.armor;
        assert.equal(armor.currentStageDurability,expected,'Каждый раунд горения восстанавливает 50 прочности');
        assert.equal(armor.stage,2,'Восстанавливается только текущая стадия');
    }
    for(let round=2;round<10;round++)await endShieldRound();
    const shieldExpired=await get();
    assert.equal(shieldExpired.data.equipment.armor.currentStageDurability,1000);
    assert.ok(!shieldExpired.data.health.effects.some(effect=>effect.artifact_stat==='armor_restore_on_hit'));
    await endShieldRound();
    assert.equal((await get()).data.equipment.armor.currentStageDurability,950,'После минуты износ снова действует');
    await request(loc+'/combat/end',{},token);
    fs.writeFileSync(path.join(safeSession.artifacts,'artifact-special.json'),JSON.stringify({scene:'Припять',artifact:rule.name,
        preparedWornSeconds:86394,realRoundSeconds:6,readyAfterRealRound:true,realButtonActivations:2,apPerUse:1,
        first:{radiation:0,hp:200,cooldown:86400},second:{radiation:0,hp:300,cooldown:86394},reopened:true,
        actionResponses,generator:{buttonActivated:true,movementBefore:-1,movementActive:-3,initialSeconds:600,afterRound:594,expired:true},
        heatHeart:{buttonActivated:true,protectedRoundDamage:0,expiredRoundDamage:50},
        fieryVein:{buttonActivated:true,preparedBreathlessness:1000,clearedBreathlessness:0,effectSeconds:18,expiredAfterThreeRounds:true},
        stormHeart:{buttonActivated:true,movement:-20,strength:5,effectiveBreathlessness:0,sleepAfterFirstMinute:552,sleepExpired:true},
        shell:{buttonActivated:true,protectedRounds:10,stageDurabilityBefore:1000,stageDurabilityProtected:1000,stageDurabilityAfterExpiry:950},
        psiImmunities:psiResults,fireGift:{realButton:true,physicalProtection:30,seconds:18,
            healthBefore:700,healthAfterTwoRounds:700,healthAfterThreeRounds:450,noRepeatedCost:true},
        liveShield:{realButton:true,stage:2,seconds:60,durability:[800,850,900,1000,950]}},null,2));
    console.log('Особые свойства: подготовленное ношение+реальный раунд, две кнопки Ядра твари по1ОД без рук, радиация→ОЗ; общий срок после первого, переоткрытие.');
    console.log('Генератор: кнопка без радиации, фактический штраф перемещения−1→−3,600→594секунд за раунд; истечение по игровому времени возвращает−1.');
    console.log('Сердце жара: настоящая кнопка и настоящее горение; первый раунд0урона, после истечения50.');
    console.log('Огненная вена: подготовленная одышка1000снята настоящей кнопкой, флаг18сек истёк за3настоящих раунда.');
    console.log('Сердце грозы: настоящая кнопка,−20/+5,оды́шка подавлена в клиентском состоянии; после12сек один сон600сек, перевод времени учитывает остаток/пробуждение.');
    console.log('Панцирь: настоящая кнопка,10 реальных раундов горения без износа брони; следующий раунд после минуты снимает50прочности.');
    console.log('Ментальный бастион и Щит сознания: настоящие кнопки, реальные Пси-удары не воздействуют, атакующий тратит3ОД; стресс блокируется Щитом и возвращается после истечения.');
    console.log('Дар пламени: настоящая кнопка, +30% физической защиты3раунда, затем ровно−250ОЗ, следующего списания нет.');
    console.log('Живой щит: настоящая кнопка, горение восстанавливает 800→850→900→1000 прочности на стадии 2; после минуты износ до 950.');
};
