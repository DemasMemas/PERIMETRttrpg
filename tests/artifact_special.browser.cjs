// Подготовленное время ношения, последний реальный раунд и две активации через обычные кнопки.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,loc,locationId,rules,safeSession})=>{
    assert.ok(safeSession.verified);assert.equal(process.env.VTT_SAFE_SCENARIO,'artifacts');
    await page.evaluate("document.querySelector('[data-pda-close]')?.click();window.closeCharacterSheet();true");
    await page.evaluate(`(()=>{
        const original=window.fetch;window.__artifactHttp=[];window.__artifactSheetHttp=[];
        window.fetch=async(...args)=>{const response=await original(...args);
            if(String(args[0]).includes('/combat/action')&&String(args[1]?.body||'').includes('artifact_special')){
                const body=await response.clone().json();window.__artifactHttp.push({status:response.status,error:body.error||body.message||null});
            }else if(String(args[0]).endsWith('/artifact-special')){
                const body=await response.clone().json();window.__artifactSheetHttp.push({status:response.status,error:body.error||body.message||null});
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
    const sphere=rules.find(row=>row.name==='Сфера притяжения'),sphereData=await get();assert.ok(sphere);
    sphereData.data.equipment.armor.containers=[{item:{id:'native-gravity-sphere',name:sphere.name,category:'artifact',quantity:1,attributes:sphere}}];
    Object.assign(sphereData.data.health,{current:700,effects:[]});
    await request(url,{data:sphereData.data},token,'PUT');
    await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:character.id},token);
    await request(loc+'/combat/start',{location_character_ids:[placed.location_character_id],
        initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
    await endShieldRound();
    await page.evaluate(`window.openCharacterSheet(${character.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Броня со Сферой притяжения');
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
    await page.until("document.querySelector('[data-artifact-special=\"native-gravity-sphere\"]:not(:disabled)')",'Сфера притяжения готова');
    await page.evaluate("document.querySelector('[data-artifact-special=\"native-gravity-sphere\"]').click();true");
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    const armed=await get();assert.equal(armed.data._artifact_specials[0].armed,true);
    assert.equal(armed.data._artifact_specials[0].remaining_uses,2);
    const fall=()=>request(loc+'/gm-events',{type:'fall',height_meters:16,location_character_ids:[placed.location_character_id]},token);
    const protectedFall=(await fall()).characters[0];
    assert.equal(protectedFall.fall.artifact,sphere.name);assert.equal(protectedFall.fall.roll,null);
    assert.equal(protectedFall.fall.shock,false);assert.equal(protectedFall.fall.fracture_both_legs,false);
    const sphereUsed=await get();assert.equal(sphereUsed.data.health.current,700);
    assert.equal(sphereUsed.data._artifact_specials[0].armed,false);
    assert.equal(sphereUsed.data._artifact_specials[0].remaining_uses,1);
    assert.equal(sphereUsed.data._artifact_specials[0].cooldown_seconds,86400);
    await request(loc+'/combat/end',{},token);
    assert.ok((await fall()).characters[0].fall.base_damage>0,'Повторное падение без выбора проходит обычно');
    const sponge=rules.find(row=>row.name==='Чернобыльская губка');assert.ok(sponge);
    const spongeBearer=await request('/lobbies/'+lobby.id+'/characters',{name:'Проверка особого свойства',data:{
        skills:{other:{survival:{base:15}}},equipment:{armor:{id:'sponge-test-armor',name:'Броня для проверки',
            containers:[{item:{id:'native-sponge',name:sponge.name,category:'artifact',quantity:1,attributes:sponge}}]}},
        health:{current:700,max:700,radiation:0,effects:[]}}},token);
    const spongeUrl='/lobbies/characters/'+spongeBearer.id,getSponge=()=>request(spongeUrl,null,token,'GET');
    const secondBearer=await request(loc+'/spawn_character',{character_id:spongeBearer.id,tile_x:43,tile_y:46},token);
    await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:spongeBearer.id},token);
    await changeMinutes(1);
    await page.evaluate(`window.openCharacterSheet(${spongeBearer.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Броня с Чернобыльской губкой');
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
    await page.until("document.querySelector('[data-artifact-special=\"native-sponge\"]:not(:disabled)')",'Чернобыльская губка готова');
    await page.evaluate("document.querySelector('[data-artifact-special=\"native-sponge\"]').click();true");
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    const sheetResponses=await page.evaluate('window.__artifactSheetHttp');
    assert.deepEqual(sheetResponses,[{status:200,error:null},{status:200,error:null}],JSON.stringify(sheetResponses));
    const immunity=data=>data.health.effects.find(effect=>effect.artifact_stat==='radiation_sickness_immunity');
    const spongeActive=await getSponge();assert.equal(immunity(spongeActive.data).remaining_seconds,14400);
    spongeActive.data.health.radiation=3;
    await request(spongeUrl,{data:spongeActive.data},token,'PUT');
    await changeMinutes(1);
    const spongeMinute=await getSponge();assert.equal(spongeMinute.data.health.current,670);
    assert.equal(spongeMinute.data.health.radiation,0);assert.equal(immunity(spongeMinute.data).remaining_seconds,14340);
    await changeMinutes(238);assert.equal(immunity((await getSponge()).data).remaining_seconds,60);
    await changeMinutes(1);assert.equal(immunity((await getSponge()).data),undefined);
    const burner=rules.find(row=>row.name==='Горелка'),burnerData=await getSponge();assert.ok(burner);
    burnerData.data.equipment.armor.containers=[{item:{id:'native-burner',name:burner.name,category:'artifact',quantity:1,attributes:burner}}];
    burnerData.data.health.current=700;
    await request(spongeUrl,{data:burnerData.data},token,'PUT');
    await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:spongeBearer.id},token);
    await changeMinutes(1);
    await page.evaluate(`window.openCharacterSheet(${spongeBearer.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Броня с Горелкой');
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
    await page.until("document.querySelector('[data-artifact-power]:not(:disabled)')",'Горелка готова');
    await page.evaluate("document.querySelector('[data-artifact-power]').value='3';document.querySelector('[data-artifact-special=\"native-burner\"]').click();true");
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    const burnerActive=await getSponge();assert.equal(burnerActive.data.health.current,550);
    assert.equal(burnerActive.data._artifact_profile.movement_penalty,-5);
    await changeMinutes(10);
    const burnerExpired=await getSponge();assert.equal(burnerExpired.data._artifact_profile.movement_penalty,-2);
    assert.equal(burnerExpired.data.health.current,550);
    const leech=rules.find(row=>row.name==='Пиявка');assert.ok(leech);
    const exposed=await request('/lobbies/'+lobby.id+'/characters',{name:'Цель Пиявки',data:{
        health:{current:700,max:700,radiation:14,effects:[]}}},token);
    const exposedUrl='/lobbies/characters/'+exposed.id;
    await request(loc+'/spawn_character',{character_id:exposed.id,tile_x:45,tile_y:46},token);
    burnerExpired.data.equipment.armor.containers=[{item:{id:'native-leech',name:leech.name,category:'artifact',quantity:1,attributes:leech}}];
    burnerExpired.data.health.radiation=9;
    await request(spongeUrl,{data:burnerExpired.data},token,'PUT');
    await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:spongeBearer.id},token);
    await changeMinutes(1);
    await page.evaluate(`window.openCharacterSheet(${spongeBearer.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Броня с Пиявкой');
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
    await page.until("document.querySelector('[data-artifact-special=\"native-leech\"]:not(:disabled)')",'Пиявка готова');
    await page.evaluate("document.querySelector('[data-artifact-special=\"native-leech\"]').click();true");
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    assert.equal((await getSponge()).data.health.radiation,0);
    assert.equal((await request(exposedUrl,null,token,'GET')).data.health.radiation,0);
    const tear=rules.find(row=>row.name==='Огненная слеза'),tearData=await getSponge();assert.ok(tear);
    tearData.data.equipment.armor.containers=[{item:{id:'native-fiery-tear',name:tear.name,category:'artifact',quantity:1,attributes:tear}}];
    await request(spongeUrl,{data:tearData.data},token,'PUT');
    await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:spongeBearer.id},token);
    await changeMinutes(1);
    const room=await request('/lobbies/'+lobby.id,null,token,'GET');
    const site=await request(loc,null,token,'GET');
    await request(loc,{lighting:{...site.lighting,mode:'world'}},token,'PUT');
    await request('/lobbies/'+lobby.id+'/time',{game_day:room.game_day,game_time_minutes:120},token,'PATCH');
    await page.evaluate("(async()=>{window.closeCharacterSheet();window.L=await import('/static/js/locationScene.js');return true;})()");
    const sun='L.sceneGraphicsForTest().sun.intensity';
    await page.until(`window.lobbyGameTimeMinutes===120&&${sun}<.15`,'На сцене два часа ночи');
    const look=id=>page.evaluate(`(()=>{L.showCombatActionMenu(650,350,${id});const b=document.querySelector('.combat-radial-action[aria-label="Взгляд"]');if(!b)return false;b.click();return true;})()`);
    assert.ok(await look(spongeBearer.id));
    const camera=(x,z)=>page.evaluate(`(()=>{const g=L.sceneGraphicsForTest();g.controls.target.set(43.5,1,46.5);g.camera.position.set(${x},14,${z});g.controls.update();return true;})()`);
    const capture=async name=>fs.writeFileSync(path.join(safeSession.artifacts,`${name}.png`),
        Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    await camera(50,57);
    const nightSun=await page.evaluate(sun);await capture('fiery-tear-night');
    await page.evaluate(`window.openCharacterSheet(${spongeBearer.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Броня с Огненной слезой');
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
    await page.until("document.querySelector('[data-artifact-special=\"native-fiery-tear\"]:not(:disabled)')",'Огненная слеза готова');
    await page.evaluate("document.querySelector('[data-artifact-special=\"native-fiery-tear\"]').click();true");
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    await page.evaluate('window.closeCharacterSheet();true');
    await page.until(`${sun}>.5`,'Носитель видит при дневном освещении');
    const daySun=await page.evaluate(sun);await capture('fiery-tear-active');
    await camera(35,51);await capture('fiery-tear-side');
    assert.ok(await look(exposed.id));
    await page.until(`${sun}<.15`,'Другой персонаж видит прежнюю ночь');
    assert.ok(await look(spongeBearer.id));
    await page.until(`${sun}>.5`,'Возврат к виду носителя');
    await camera(50,57);
    await changeMinutes(10);
    await page.until(`${sun}<.15`,'Через десять минут вид носителя снова ночной');
    assert.equal(await page.evaluate('window.lobbyGameTimeMinutes'),130);
    await capture('fiery-tear-expired');
    const current=rules.find(row=>row.name==='Живой ток'),currentData=await getSponge();assert.ok(current);
    currentData.data.equipment.armor.containers=[{item:{id:'native-living-current',name:current.name,category:'artifact',quantity:1,attributes:current}}];
    await request(spongeUrl,{data:currentData.data},token,'PUT');
    await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:spongeBearer.id},token);
    await changeMinutes(1);
    await request(loc+'/spawn_character',{character_id:spongeBearer.id,tile_x:45,tile_y:62,force_relocate:true},token);
    await request(loc+'/combat/start',{location_character_ids:[secondBearer.location_character_id],
        initiator_location_character_id:secondBearer.location_character_id,initiator_first:true},token);
    await page.evaluate(`window.openCharacterSheet(${spongeBearer.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Броня с Живым током');
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
    await page.until("document.querySelector('[data-artifact-special=\"native-living-current\"]:not(:disabled)')",'Живой ток готов');
    await page.evaluate("document.querySelector('[data-artifact-special=\"native-living-current\"]').click();true");
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    const liveState=()=>request(loc+'/combat',null,token,'GET');
    const liveBefore=(await liveState()).current_character;
    assert.equal(liveBefore.jump_preview.max_distance,5);
    const preview=await request(loc+'/movement-preview',{character_id:spongeBearer.id,special_action:'jump',x:45,y:67},token);
    assert.equal(preview.jump.max_distance,5);assert.equal(preview.route.distance,5);
    const liveJump=await request(loc+'/combat/action',{location_character_id:secondBearer.location_character_id,action_key:'jump',target_x:45,target_y:67},token);
    assert.equal(liveJump.jump.distance,5);
    assert.equal(liveJump.character.action_points_current,liveBefore.action_points_current-2);
    for(let round=0;round<2;round++)await request(loc+'/combat/end_turn',{location_character_id:secondBearer.location_character_id},token);
    const liveAfter=(await liveState()).current_character;
    assert.ok(liveAfter.jump_preview.max_distance<5);
    await request(loc+'/combat/end',{},token);
    const leaf=rules.find(row=>row.name==='Философский лист'),leafData=await getSponge();assert.ok(leaf);
    leafData.data.equipment.armor.containers=[{item:{id:'native-philosophers-leaf',name:leaf.name,category:'artifact',quantity:1,attributes:leaf}}];
    leafData.data.skills.physical.agility={base:30};leafData.data.health.current=400;
    await request(spongeUrl,{data:leafData.data},token,'PUT');
    await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:spongeBearer.id},token);
    await changeMinutes(1);
    await request(loc+'/spawn_character',{character_id:spongeBearer.id,tile_x:45,tile_y:62,force_relocate:true},token);
    await request(loc+'/objects',{name:'Вспышка',type:'anomaly',tile_x:45,tile_y:63,properties:{anomaly_key:'vspishka'}},token);
    await request(loc+'/combat/start',{location_character_ids:[secondBearer.location_character_id],
        initiator_location_character_id:secondBearer.location_character_id,initiator_first:true},token);
    await page.evaluate(`window.openCharacterSheet(${spongeBearer.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Броня с Философским листом');
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
    await page.until("document.querySelector('[data-artifact-special=\"native-philosophers-leaf\"]:not(:disabled)')",'Философский лист готов');
    await page.evaluate("document.querySelector('[data-artifact-special=\"native-philosophers-leaf\"]').click();true");
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    const intoFlash=await request(loc+'/combat/action',{location_character_id:secondBearer.location_character_id,action_key:'jump',target_x:45,target_y:63},token);
    assert.equal(intoFlash.jump.outcome,'success');
    await request(loc+'/combat/end_turn',{location_character_id:secondBearer.location_character_id},token);
    assert.equal((await getSponge()).data.health.current,600,'Вспышка исцеляет на 200 ОЗ');
    await request(loc+'/combat/end',{},token);
    const rod=rules.find(row=>row.name==='Громоотвод'),rodData=await getSponge();assert.ok(rod);
    rodData.data.equipment.armor.containers=[{item:{id:'native-lightning-rod',name:rod.name,category:'artifact',quantity:1,attributes:rod}}];
    await request(spongeUrl,{data:rodData.data},token,'PUT');
    await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:spongeBearer.id},token);
    await changeMinutes(1);
    await request(loc+'/spawn_character',{character_id:spongeBearer.id,tile_x:45,tile_y:62,force_relocate:true},token);
    await request(loc+'/spawn_character',{character_id:exposed.id,tile_x:45,tile_y:67,force_relocate:true},token);
    await page.evaluate(`window.openCharacterSheet(${spongeBearer.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Броня с Громоотводом');
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
    await page.until("document.querySelector('[data-artifact-special=\"native-lightning-rod\"]:not(:disabled)')",'Громоотвод готов');
    await page.evaluate("document.querySelector('[data-artifact-special=\"native-lightning-rod\"]').click();true");
    const targetChoice="[...document.querySelectorAll('#consumable-application-modal .consumable-application-list button')].find(button=>button.textContent==='Цель Пиявки')";
    await page.until(targetChoice,'В выборе доступна видимая цель');
    await page.evaluate(`${targetChoice}.click();true`);
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    assert.equal((await request(exposedUrl,null,token,'GET')).data.health.current,500);
    assert.equal((await getSponge()).data._artifact_specials[0].remaining_uses,1);
    const conductor=rules.find(row=>row.name==='Проводник бури'),conductorData=await getSponge();assert.ok(conductor);
    conductorData.data.equipment.armor.containers=[{item:{id:'native-storm-conductor',name:conductor.name,
        category:'artifact',quantity:1,attributes:conductor}}];
    await request(spongeUrl,{data:conductorData.data},token,'PUT');
    await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:spongeBearer.id},token);
    await changeMinutes(1);
    await page.evaluate(`window.openCharacterSheet(${spongeBearer.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Броня с Проводником бури');
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
    await page.until("document.querySelector('[data-artifact-special=\"native-storm-conductor\"]:not(:disabled)')",'Проводник бури готов');
    await page.evaluate("document.querySelector('[data-artifact-special=\"native-storm-conductor\"]').click();true");
    await page.until(targetChoice,'Проводник бури предлагает видимую цель');
    await page.evaluate(`${targetChoice}.click();true`);
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    assert.equal((await request(exposedUrl,null,token,'GET')).data.health.current,150);
    assert.equal((await getSponge()).data._artifact_specials[0].remaining_uses,1);
    const magnet=rules.find(row=>row.name==='Ферромагнит'),magnetData=await getSponge();assert.ok(magnet);
    magnetData.data.equipment.armor.containers=[{item:{id:'native-ferromagnet',name:magnet.name,
        category:'artifact',quantity:1,attributes:magnet}}];
    await request(spongeUrl,{data:magnetData.data},token,'PUT');
    await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:spongeBearer.id},token);
    const magnetClock=await request('/lobbies/'+lobby.id,null,token,'GET');
    await changeMinutes(720-magnetClock.game_time_minutes);
    await request(loc+'/objects',{name:'Пол',type:'ground_item',tile_x:45,tile_y:64,
        properties:{is_ground_item:true,passable:true,floor_level:0,contents:[
            {id:'metal-stack',name:'Металлические детали',category:'material',quantity:2,weight:.2,volume:.1},
            {id:'glass',name:'Стекло',category:'material',quantity:1,weight:.2,volume:.1},
        ]}},token);
    await page.evaluate(`window.openCharacterSheet(${spongeBearer.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Броня с Ферромагнитом');
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
    const magnetButton='document.querySelector(\'[data-artifact-special="native-ferromagnet"]:not(:disabled)\')';
    await page.until(magnetButton,'Ферромагнит готов');
    await page.evaluate(`${magnetButton}.click();true`);
    const magnetChoices="[...document.querySelectorAll('#consumable-application-modal .consumable-application-list button')]";
    await page.until(`${magnetChoices}.some(button=>button.textContent.includes('Металлические детали'))`,'Металл виден в выборе');
    assert.equal(await page.evaluate(`${magnetChoices}.some(button=>button.textContent.includes('Стекло'))`),false);
    await page.evaluate(`${magnetChoices}.find(button=>button.textContent.includes('Металлические детали')).click();true`);
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    const magnetAfter=(await getSponge()).data;
    assert.equal(magnetAfter.inventory.held[0].name,'Металлические детали');
    assert.equal(magnetAfter.inventory.held[0].quantity,1);
    assert.equal(magnetAfter._artifact_specials[0].remaining_uses,1);
    await page.evaluate(`window.openCharacterSheet(${spongeBearer.id},'inventory').then(()=>true)`);
    await page.until("document.querySelector('#held-items-container')?.textContent.includes('Металлические детали')",'Притянутый предмет виден в руках');
    await page.evaluate(`(()=>{
        const transfer=new DataTransfer();
        document.querySelector('#held-items-container [draggable=true]').dispatchEvent(new DragEvent('dragstart',{bubbles:true,dataTransfer:transfer}));
        document.querySelector('#pockets-container').dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:transfer}));
        return true;
    })()`);
    await page.until("document.querySelector('#pockets-container input[data-widget-key=name]')?.value==='Металлические детали'",'Предмет перетащен в карман');
    await page.evaluate(`import('/static/js/api.js').then(({Server})=>Server.waitForCharacterSave(${spongeBearer.id})).then(()=>true)`);
    const magnetStored=(await getSponge()).data.inventory;
    assert.equal(magnetStored.held.length,0);
    assert.ok(magnetStored.pockets.some(item=>item.id===magnetAfter.inventory.held[0].id));
    const motionData=await getSponge();
    motionData.data.equipment.armor.containers=['Алхимический веер','Жгут плазмы'].map((name,index)=>{
        const entry=rules.find(row=>row.name===name);assert.ok(entry);
        return {item:{id:`native-motion-${index}`,name,category:'artifact',quantity:1,attributes:entry}};
    });
    await request(spongeUrl,{data:motionData.data},token,'PUT');
    await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:spongeBearer.id},token);
    await changeMinutes(1);
    await page.evaluate(`window.openCharacterSheet(${spongeBearer.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Броня с Алхимическим веером');
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
    const motionButton=index=>`document.querySelector('[data-artifact-special="native-motion-${index}"]:not(:disabled)')`;
    await page.until(motionButton(0),'Веер готов');
    await page.evaluate(`${motionButton(0)}.click();true`);
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    const cloud=`L.sceneGraphicsForTest().scene.children.find(node=>node.userData.combatArea?.character_id===${spongeBearer.id})`;
    await page.until(`${cloud}?.visible`,'Кристаллическое облако видно на сцене');
    const cloudBefore=await page.evaluate(`${cloud}.position.z`);
    const target=await page.evaluate(`(()=>{
        const g=L.sceneGraphicsForTest(), actor=g.characters.get(${spongeBearer.id}).model.position;
        g.controls.target.set(45.5,actor.y,63.5);g.camera.position.set(50,actor.y+12,68);g.controls.update();g.camera.updateMatrixWorld(true);
        const rect=g.renderer.domElement.getBoundingClientRect();
        const point=actor.clone().set(45.5,actor.y,64.5).project(g.camera);
        return {x:Math.round(rect.left+(point.x+1)*rect.width/2),y:Math.round(rect.top+(1-point.y)*rect.height/2)};
    })()`);
    await page.until(motionButton(1),'Жгут плазмы готов');
    await page.evaluate(`${motionButton(1)}.click();true`);
    await page.until("document.getElementById('character-sheet-modal').style.visibility==='hidden'",'Выбор точки открыт');
    await page.call('Input.dispatchMouseEvent',{type:'mousePressed',...target,button:'left',buttons:1,clickCount:1});
    await page.call('Input.dispatchMouseEvent',{type:'mouseReleased',...target,button:'left',buttons:0,clickCount:1});
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    await page.until(`${cloud}?.position.z>${cloudBefore+1}`,'Облако переместилось за владельцем');
    const movedState=await request(loc+'/combat',null,token,'GET');
    const plasmaOwner=movedState.characters.find(row=>row.character_id===spongeBearer.id);
    assert.ok(Math.hypot(plasmaOwner.x-45,plasmaOwner.y-64)<.35);
    assert.equal((await getSponge()).data._artifact_specials.find(row=>row.item_id==='native-motion-1').remaining_uses,1);
    await page.evaluate('window.closeCharacterSheet();true');
    await capture('artifact-crystal-cloud');
    await page.evaluate(`(()=>{const g=L.sceneGraphicsForTest();g.camera.position.set(38,9,59);g.controls.update();return true;})()`);
    await capture('artifact-crystal-cloud-side');
    await changeMinutes(1);
    await page.until(`!(${cloud})`,'Облако исчезло по игровому времени');
    const equipSpecial=async(name,id)=>{
        const special=rules.find(row=>row.name===name),fresh=await getSponge();assert.ok(special);
        fresh.data.equipment.armor.containers=[{item:{id,name,category:'artifact',quantity:1,attributes:special}}];
        await request(spongeUrl,{data:fresh.data},token,'PUT');
        await request('/__testing/artifact-preparation/'+safeSession.runId,{character_id:spongeBearer.id},token);
        await changeMinutes(1);
    };
    const clickSpecial=async(id)=>{
        await page.evaluate(`window.openCharacterSheet(${spongeBearer.id},'equipment').then(()=>true)`);
        await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Броня с проверяемым артефактом');
        await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
        const button=`document.querySelector('[data-artifact-special="${id}"]:not(:disabled)')`;
        await page.until(button,'Артефакт готов');
        await page.evaluate(`${button}.click();true`);
        await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    };
    await equipSpecial('Полюс','native-pole');
    await request(loc+'/spawn_character',{character_id:spongeBearer.id,tile_x:45,tile_y:62,force_relocate:true},token);
    const relocateExposed=(x,y)=>request(loc+'/spawn_character',{character_id:exposed.id,tile_x:x,tile_y:y,force_relocate:true},token);
    await relocateExposed(30,67);
    const getExposed=()=>request(exposedUrl,null,token,'GET');
    const lampData=await getExposed();
    lampData.data.inventory ||= {};
    lampData.data.inventory.pockets=[{id:'pole-lamp',name:'Фонарик',attributes:{portable_light:'hand'}}];
    await request(exposedUrl,{data:lampData.data},token,'PUT');
    for(const operation of ['prepare','toggle']){
        await request(exposedUrl+'/portable-lights',{operation,mount:'hand',path:['inventory','pockets',0],source_id:'pole-lamp',
            character_revision:(await getExposed()).data._revision},token);
    }
    const lampState=`window.locationCombatState?.characters?.find(row=>row.character_id===${exposed.id})?.portable_lights`;
    await page.until(`${lampState}?.length===1`,'Фонарь вне радиуса работает');
    await relocateExposed(45,67);
    await page.until(`${lampState}?.length===0`,'В зоне Полюса фонарь погас');
    await relocateExposed(30,67);
    await page.until(`${lampState}?.length===1`,'За радиусом фонарь снова работает');
    await relocateExposed(45,67);
    await request(loc+'/objects',{name:'Пол',type:'ground_item',tile_x:45,tile_y:65,
        properties:{is_ground_item:true,passable:true,floor_level:0,contents:[
            {id:'pole-metal',name:'Стальной предмет',category:'material',metallic:true,quantity:1,weight:5},
        ]}},token);
    await clickSpecial('native-pole');
    const locationAfterPole=await request(loc,null,token,'GET');
    assert.ok(locationAfterPole.objects.some(obj=>obj.tile_x===45&&obj.tile_y===62&&obj.properties.contents?.some(item=>item.id==='pole-metal')));
    assert.equal((await getExposed()).data.inventory.pockets[0].electronicsState.burned,true);
    await equipSpecial('Ядро тяготения','native-gravity-hit');
    await request(loc+'/spawn_character',{character_id:spongeBearer.id,tile_x:45,tile_y:64,force_relocate:true},token);
    const gravityTarget=await relocateExposed(45,65);
    const gravityTargetSheet=await getExposed();
    gravityTargetSheet.data.health.current=700;
    gravityTargetSheet.data.health.effects=[{type:'sleep',name:'Сон',source:'artifact-target-sleep',tick:'manual',active:true}];
    await request(exposedUrl,{data:gravityTargetSheet.data},token,'PUT');
    await request(loc+'/combat/start',{location_character_ids:[secondBearer.location_character_id,gravityTarget.location_character_id],
        initiator_location_character_id:secondBearer.location_character_id,initiator_first:true},token);
    const gravityBefore=(await liveState()).current_character.action_points_current;
    await clickSpecial('native-gravity-hit');
    assert.equal((await liveState()).current_character.action_points_current,gravityBefore-1);
    const blow=await request(loc+'/combat/action',{location_character_id:secondBearer.location_character_id,
        action_key:'attack',attack_type:'unarmed',weapon_index:-1,target_character_id:exposed.id},token);
    const strike=blow.attack.results.find(hit=>hit.artifact_hit==='Ядро тяготения');
    assert.ok(strike?.hit&&strike.zone==='chest'&&strike.base_damage===100);
    const thrownTarget=blow.state.characters.find(row=>row.character_id===exposed.id);
    assert.ok(thrownTarget.y>65);
    assert.equal(blow.character.action_points_current,gravityBefore-3);
    assert.equal((await getSponge()).data._artifact_specials[0].remaining_uses,1);
    await request(loc+'/combat/end',{},token);
    await page.evaluate('window.closeCharacterSheet();true');
    const auraClock=await request('/lobbies/'+lobby.id,null,token,'GET');
    await request('/lobbies/'+lobby.id+'/time',{game_day:auraClock.game_day,game_time_minutes:120},token,'PATCH');
    await page.until(`window.lobbyGameTimeMinutes===120&&${sun}<.15`,'Ночь для ауры возгорания');
    const auraCamera=(x,z)=>page.evaluate(`(()=>{const g=L.sceneGraphicsForTest();
        g.controls.target.set(45.5,1,64.5);g.camera.position.set(${x},9,${z});g.controls.update();return true;})()`);
    const auraLight="L.sceneGraphicsForTest().scene.children.find(child=>child.isPointLight&&child.color.getHexString()==='ffab64')";
    await auraCamera(52,71);
    await capture('ignition-aura-before');
    await equipSpecial('Ядро возгорания','native-ignition-aura');
    await page.until(`${auraLight}?.distance===5&&${auraLight}?.intensity===5`,'Появилась тёплая аура радиусом 5 м');
    const auraBefore=await page.evaluate(`${auraLight}.position.z`);
    await capture('ignition-aura-active');
    await request(loc+'/spawn_character',{character_id:spongeBearer.id,tile_x:45,tile_y:62,force_relocate:true},token);
    await page.until(`${auraLight}?.position.z<${auraBefore-1}`,'Свет следует за носителем');
    await auraCamera(39,67);
    await capture('ignition-aura-side');
    const withoutAura=await getSponge();
    withoutAura.data.equipment.armor.containers=[];
    await request(spongeUrl,{data:withoutAura.data},token,'PUT');
    await page.until(`!(${auraLight})`,'Снятие артефакта убрало свет');

    await changeMinutes(600);
    const sharedSightTarget=await relocateExposed(45,67);
    const sharedSightActor=await request(loc+'/spawn_character',{character_id:spongeBearer.id,tile_x:45,tile_y:39,force_relocate:true},token);
    const spotter=await request('/lobbies/'+lobby.id+'/characters',{name:'Разведчик Проводника',data:{}},token);
    const sharedSightSpotter=await request(loc+'/spawn_character',{character_id:spotter.id,tile_x:45,tile_y:66},token);
    assert.ok(sharedSightTarget.location_character_id);
    await request(loc+'/characters/'+spotter.id+'/facing',{facing_x:0,facing_y:1},token,'PATCH');
    const assignTeam=team=>request(loc+'/teams',{assignments:[sharedSightActor,sharedSightSpotter]
        .map(entry=>({location_character_id:entry.location_character_id,team_name:team}))},token,'PUT');
    const targetInSharedSight=`L.visibleArtifactTargets(${spongeBearer.id},35,{sharedSight:true}).some(row=>row.id===${exposed.id})`;
    await assignTeam('');
    assert.equal(await page.evaluate(targetInSharedSight),false);
    await assignTeam('Разведка Проводника');
    await equipSpecial('Проводник бури','native-conductor-shared-sight');
    await page.until(targetInSharedSight,'Союзник видит цель на расстоянии 28 м от владельца');
    const sharedSightSheet=await getExposed();
    sharedSightSheet.data.health.current=500;
    await request(exposedUrl,{data:sharedSightSheet.data},token,'PUT');
    await page.evaluate(`window.openCharacterSheet(${spongeBearer.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Броня с Проводником бури');
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
    const sharedButton='document.querySelector(\'[data-artifact-special="native-conductor-shared-sight"]:not(:disabled)\')';
    await page.until(sharedButton,'Проводник готов к применению');
    await page.evaluate(`${sharedButton}.click();true`);
    await page.until(targetChoice,'Дальняя цель появилась в выборе');
    await page.evaluate(`${targetChoice}.click();true`);
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    assert.equal((await getExposed()).data.health.current,150);

    const forecastRule=rules.find(row=>row.name==='Тень чужого');
    const forecastSheet=await getSponge();
    forecastSheet.data.equipment.armor.containers=[{item:{id:'native-forecast-one',name:forecastRule.name,
        category:'artifact',quantity:1,attributes:forecastRule}}];
    forecastSheet.data.skills.physical.will={base:10,bonus:0,xp:0};
    forecastSheet.data.health.psyState=forecastSheet.data.health.psy_state=0;
    await request(spongeUrl,{data:forecastSheet.data},token,'PUT');
    await request(loc+'/spawn_character',{character_id:spongeBearer.id,tile_x:45,tile_y:64,force_relocate:true},token);
    await request(loc+'/characters/'+spongeBearer.id+'/facing',{facing_x:0,facing_y:1},token,'PATCH');
    await request(loc+'/combat/start',{location_character_ids:[sharedSightActor.location_character_id,sharedSightTarget.location_character_id],
        initiator_location_character_id:sharedSightActor.location_character_id,initiator_first:true},token);
    const forecastBefore=(await liveState()).current_character.action_points_current;
    await page.evaluate(`window.openCharacterSheet(${spongeBearer.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.equipment-panel-tab[data-panel=armor]')",'Броня с Тенью чужого');
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=armor]').click();true");
    const forecastButton='document.querySelector(\'[data-artifact-forecast="native-forecast-one"]:not(:disabled)\')';
    await page.until(forecastButton,'Предвидение доступно без времени ношения');
    await page.evaluate(`${forecastButton}.click();true`);
    await page.until(targetChoice,'В предвидении доступна одна цель');
    assert.equal(await page.evaluate("document.querySelectorAll('#consumable-application-modal .consumable-application-list button').length"),1);
    await page.evaluate(`${targetChoice}.click();true`);
    await page.evaluate('window.__artifactUsePromise.then(()=>true)');
    await page.until("[...document.querySelectorAll('.notification')].some(el=>el.textContent.includes('Намерения на следующий ход')&&el.textContent.includes('Цель Пиявки'))",
        'ГМ получил запрос о намерениях выбранного противника');
    const forecastAfter=await getSponge();
    const psiPrice=forecastAfter.data.health.psyState;
    assert.ok(psiPrice>5,'Отрицательная пси-защита увеличивает цену');
    assert.equal(forecastAfter.data._artifact_specials[0].remaining_uses,2);
    assert.equal((await liveState()).current_character.action_points_current,forecastBefore-2);
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
        liveShield:{realButton:true,stage:2,seconds:60,durability:[800,850,900,1000,950]},
        gravitySphere:{realButton:true,height:16,roll:null,damage:0,shock:false,fractures:false,remainingUses:1},
        sponge:{realButton:true,seconds:14400,radiationBefore:3,radiationAfter:0,ownCost:30,expired:true},
        burner:{realButton:true,power:3,seconds:600,healthSpent:150,movementDuring:-5,movementAfter:-2},
        leech:{realButton:true,radius:3,ownerRadiation:0,neighborRadiation:0},
        fieryTear:{realButton:true,nightSun,daySun,seconds:600,otherCharacterRemainsDark:true},
        livingCurrent:{realButton:true,distance:5,jumpAp:2,expiredAfterRounds:2},
        philosophersLeaf:{realButton:true,realAnomaly:'Вспышка',healthBefore:400,healthAfter:600},
        lightningRod:{realButton:true,targetSelected:true,damage:200,healthAfter:500},
        stormConductor:{realButton:true,targetSelected:true,damage:350,healthAfter:150},
        ferromagnet:{realButton:true,metalSelected:true,glassExcluded:true,oneUnitInHand:true,movedToPocket:true,remainingUses:1},
        crystalCloud:{realButton:true,visible:true,movedWithBearer:true,expired:true},
        plasmaCord:{realButton:true,groundClick:true,position:[plasmaOwner.x,plasmaOwner.y],remainingUses:1},
        pole:{realButton:true,groundTransfer:true,electronicsDestroyed:true,nearbyLampRestoredOutsideRadius:true},
        gravityCore:{realButton:true,baseDamage:strike.base_damage,zone:strike.zone,destination:[thrownTarget.x,thrownTarget.y],totalActionPoints:3},
        ignitionAura:{radius:5,night:true,movedWithBearer:true,removedWithArtifact:true},
        stormConductorSharedSight:{realButton:true,distance:28,damage:350},
        singleForecast:{realButton:true,actionPoints:2,psiPrice,dailyChargesSpent:0,gmNotice:true}},null,2));
    console.log('Особые свойства: подготовленное ношение+реальный раунд, две кнопки Ядра твари по1ОД без рук, радиация→ОЗ; общий срок после первого, переоткрытие.');
    console.log('Генератор: кнопка без радиации, фактический штраф перемещения−1→−3,600→594секунд за раунд; истечение по игровому времени возвращает−1.');
    console.log('Сердце жара: настоящая кнопка и настоящее горение; первый раунд0урона, после истечения50.');
    console.log('Огненная вена: подготовленная одышка1000снята настоящей кнопкой, флаг18сек истёк за3настоящих раунда.');
    console.log('Сердце грозы: настоящая кнопка,−20/+5,оды́шка подавлена в клиентском состоянии; после12сек один сон600сек, перевод времени учитывает остаток/пробуждение.');
    console.log('Панцирь: настоящая кнопка,10 реальных раундов горения без износа брони; следующий раунд после минуты снимает50прочности.');
    console.log('Ментальный бастион и Щит сознания: настоящие кнопки, реальные Пси-удары не воздействуют, атакующий тратит3ОД; стресс блокируется Щитом и возвращается после истечения.');
    console.log('Дар пламени: настоящая кнопка, +30% физической защиты3раунда, затем ровно−250ОЗ, следующего списания нет.');
    console.log('Живой щит: настоящая кнопка, горение восстанавливает 800→850→900→1000 прочности на стадии 2; после минуты износ до 950.');
    console.log('Сфера притяжения: настоящая кнопка, падение с 16 м без урона, шока и переломов; следующий удар без Сферы.');
    console.log('Чернобыльская губка: настоящая кнопка, защита действует 4 игровых часа, собственная цена за выведенные 3 ед. радиации составляет 30 ОЗ.');
    console.log('Горелка: выбор −3 и 150 ОЗ в интерфейсе; общий штраф −5 возвращается к −2 через 10 минут.');
    console.log('Пиявка: реальная кнопка убирает всю радиацию владельца и второго персонажа на расстоянии двух метров.');
    console.log('Огненная слеза: личный дневной вид ночью, переключение на второго персонажа и восстановление ночного вида через 10 минут.');
    console.log('Живой ток: настоящая кнопка и прыжок на 5 м за 2 ОД. Через два раунда возвращается обычная дальность.');
    console.log('Философский лист: настоящая кнопка, прыжок во Вспышку, завершение хода и 400→600 общих ОЗ.');
    console.log('Громоотвод: выбор видимой цели в интерфейсе и 200 электрического урона.');
    console.log('Ферромагнит: выбор металла без стекла, одна единица отображается в руках и перетаскивается в карман.');
};
