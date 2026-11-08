// Три настоящих попадания. Случайные числа заданы только в изолированной fixture.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,loc,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'effects');assert.ok(safeSession.verified);
    const evidence=require('./helpers/playtest_registry.cjs').evidenceWriter(safeSession,'pepper-head');
    const target=await request(`/lobbies/${lobby.id}/characters`,{name:'Цель Перцовки',data:{
        skills:{physical:{will:{base:20},strength:{base:20}}},health:{painLevel:0}}},token);
    const shooter=await request(`/lobbies/${lobby.id}/characters`,{name:'Стрелок Перцовки',data:{
        skills:{physical:{shooting:{base:100},strength:{base:20}}},weapons:[{id:'pepper-proof-weapon',name:'Контрольное оружие (урон 5)',
            range:30,ergonomics:100,damage:5,ammo:20,durability:100,maxDurability:100,
            installedMagazine:{id:'pepper-proof-mag',name:'Контрольный магазин',ammo:[{id:'pepper-proof-ammo',name:'18x45 И',quantity:20,
                damage:5,attributes:{caliber:'18x45 И',damage:5}}]}}]}},token);
    const placed=await request(loc+'/spawn_character',{character_id:target.id,tile_x:46,tile_y:48},token);
    const gunner=await request(loc+'/spawn_character',{character_id:shooter.id,tile_x:42,tile_y:48},token);
    await request(loc+'/characters/'+target.id+'/facing',{facing_x:-1,facing_y:0},token,'PATCH');
    await request(loc+'/characters/'+shooter.id+'/facing',{facing_x:1,facing_y:0},token,'PATCH');
    await page.evaluate(`window.openCharacterSheet(${target.id},'basic').then(()=>true)`);
    await request(loc+'/combat/start',{location_character_ids:[gunner.location_character_id,placed.location_character_id],
        initiator_location_character_id:gunner.location_character_id,initiator_first:true},token);
    const command={location_character_id:gunner.location_character_id,weapon_index:0,target_character_id:target.id};
    await request(loc+'/combat/action',{...command,action_key:'draw_weapon'},token);
    await request(loc+'/combat/end_turn',{location_character_id:gunner.location_character_id},token);
    await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
    let health;
    for(let count=1;count<=3;count++){
        await request(loc+'/combat/action',{...command,action_key:'aim'},token);
        const state=await request(loc+'/combat',null,token,'GET'),cost=state.current_character.weapon_ergonomics[0].aimed_shot_action_points;
        const result=await request(loc+'/combat/action',{...command,action_key:'attack',fire_mode:'aimed',target_zone:'head',
            shot_count:1,volley_count:1,action_points:cost},token),hit=result.attack.results[0];
        assert.equal(hit.pepper_head_hit.hits,count);assert.equal(hit.pepper_head_hit.shock,count===3);
        assert.equal(hit.pepper_head_hit.shock_roll,count===3?null:99);
        const current=await request('/lobbies/characters/'+target.id,null,token,'GET');health=current.data.health;
        assert.equal(health.combatMeta.pepperHeadHits,count);assert.equal(health.combatMeta.pepperSkipTurns,1);
        assert.ok(health.effects.some(e=>e.source==='pepper_head_rolls'&&e.rollPenalty===5));
        assert.ok(health.effects.some(e=>e.source==='pepper_head_pain'&&e.value===1&&e.tick==='turn_end'));
        evidence.step('Попадание в голову '+count+' из 3',
            {headHits:count-1,damage:5,armor:'Нет',distanceMeters:4},
            {headHits:health.combatMeta.pepperHeadHits,shock:hit.pepper_head_hit.shock,shockRoll:hit.pepper_head_hit.shock_roll,
                skippedTurns:health.combatMeta.pepperSkipTurns,rollPenalty:health.effects.find(e=>e.source==='pepper_head_rolls').rollPenalty,
                painPerTurn:health.effects.find(e=>e.source==='pepper_head_pain').value,
                effects:health.effects.filter(e=>e.source?.startsWith('pepper')).map(e=>({name:e.name,remaining:e.remaining??null,timeUnit:e.time_unit??null}))});
        if(count<3){
            const painBefore=health.painLevel,ended=await request(loc+'/combat/end_turn',{location_character_id:gunner.location_character_id},token);
            assert.ok(ended.auto_skipped?.some(entry=>entry.location_character_id===placed.location_character_id),'Ход цели автоматически пропущен');
            const next=await request(loc+'/combat',null,token,'GET');assert.equal(next.current_character.location_character_id,gunner.location_character_id);
            const updated=await request('/lobbies/characters/'+target.id,null,token,'GET');
            assert.ok(updated.data.health.painLevel>=painBefore+1,'В пропущенный ход добавляется боль');
            evidence.step('Следующий ход цели после попадания '+count,
                {painLevel:painBefore,skippedTurns:1},
                {painLevel:updated.data.health.painLevel,turnSkipped:true,currentActor:next.current_character.name});
        }
    }
    assert.ok(health.effects.some(e=>e.source==='pepper_three_head_hits'&&e.remaining===6&&e.time_unit==='hour'));
    assert.ok(health.effects.some(e=>e.source==='pepper_day_penalty'&&e.remaining===24&&e.time_unit==='hour'));
    await page.until("document.getElementById('character-sheet-modal')?.textContent.includes('Перцовка: болевой шок на 6 часов')",'Эффект пришёл в открытый лист по сети');
    await page.evaluate("(()=>{const card=[...document.querySelectorAll('#health-right-column .health-effect-card')].find(e=>e.textContent.includes('Перцовка: болевой шок на 6 часов'));if(!card)throw Error('Нет карточки эффекта');let parent=card.parentElement;while(parent){if(parent.tagName==='DETAILS')parent.open=true;parent=parent.parentElement;}card.scrollIntoView({block:'center'});return true})()");
    await page.until("(()=>{const c=[...document.querySelectorAll('#health-right-column .health-effect-card')].find(e=>e.textContent.includes('Перцовка: болевой шок на 6 часов'));const r=c.getBoundingClientRect();return r.width>0&&r.y>=0&&r.bottom<=innerHeight})()",'Шок действительно виден в игровом интерфейсе');
    const descriptions=await page.evaluate("[...document.querySelectorAll('#health-right-column .health-effect-card')].filter(e=>e.textContent.includes('Перцовка:')).map(e=>e.textContent)");
    assert.ok(descriptions.some(text=>text.includes('−5 ко всем броскам до конца боя.')));
    assert.ok(descriptions.some(text=>text.includes('−5 к броскам атаки и Внимательности до окончания эффекта.')));
    assert.ok(descriptions.find(text=>text.includes('боле')&&text.includes('6 часов')).includes('выйти из болевого шока нельзя.'));
    assert.ok(!descriptions.some(text=>text.includes('Последствия определяются описанием')||text.includes('кроме попытки очнуться')));
    const shot='pepper-head.png';fs.writeFileSync(path.join(safeSession.artifacts,shot),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    await request(loc+'/combat/end',{},token);
    evidence.publish({images:[shot],controls:{random:'к20 = 4; к100 = 99. Нет случайного раннего шока. Третий шок не требует броска.',time:'Между попаданиями штатно меняются ходы; 6 и 24 часа не проматываются'},
        limitations:['Контрольное оружие и патрон имеют урон 5, чтобы не смешивать проверку свойства с гибелью цели; боевой урон каталога здесь не подтверждается.',
            'Проверены пропуск следующего хода, боль и назначенные сроки. Истечение шестичасового шока и суточного штрафа — отдельные случаи.',
            'Один клиент ГМа; отдельного клиента игрока нет. Отдельно нужен случай успешного 50% шока.']});
    console.log('Перцовка: три полных попадания в голову, известный бросок 99, счётчик/пропуск/штраф/боль, гарантированный шок 6 часов и суточные штрафы, сетевой открытый лист.');
};
