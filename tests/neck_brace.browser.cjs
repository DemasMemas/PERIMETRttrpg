// Фиксатор шеи: штатная установка, непробитие и перезарядка через интерфейс.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,offered,loc,npc,shooter,gunner,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'catalog');assert.ok(safeSession.verified);
    await request(loc+'/combat/end',{},token);
    const template=offered.global.find(t=>t.category==='helmet'&&t.attributes?.modification_category==='Шлема с забралом');
    assert.ok(template);
    const helmet={id:'brace-browser-helmet',templateId:template.id,name:template.name,category:'helmet',quantity:1,
        attributes:structuredClone(template.attributes),protection:structuredClone(template.attributes.protection),
        durability:100,maxDurability:100,stage:1,stageDurability:1000,currentStageDurability:1000};
    const owner=await request(`/lobbies/${lobby.id}/characters`,{name:'Испытатель фиксатора шеи',data:{equipment:{helmet},
        skills:{other:{engineering:{base:100}}},inventory:{money:100000,pockets:[],backpack:[]}}},token);
    const placement=await request(loc+'/spawn_character',{character_id:owner.id,tile_x:45,tile_y:48},token);
    await request(loc+'/spawn_character',{character_id:shooter.id,tile_x:44,tile_y:48},token);
    await request(loc+'/characters/'+owner.id+'/facing',{facing_x:-1,facing_y:0},token,'PATCH');
    const current=await request('/lobbies/characters/'+owner.id,null,token,'GET');
    const master=await request('/lobbies/characters/'+npc.id,null,token,'GET');
    // После переноса стрелок и мастер стоят по разные стороны испытуемого: мастер должен быть рядом для услуги.
    await request(loc+'/spawn_character',{character_id:npc.id,tile_x:45,tile_y:49},token);
    await request(loc+'/professional-modification',{actor_location_character_id:placement.location_character_id,
        specialist_character_id:npc.id,target_path:['equipment','helmet'],item_id:helmet.id,rule_id:'armor-g37',execute:true,
        operation_id:'browser-neck-install',actor_revision:current.data._revision,specialist_revision:master.data._revision},token);
    await page.evaluate(`window.openCharacterSheet(${owner.id},'equipment').then(()=>true)`);
    await page.until("document.querySelector('.loadout-slot[data-equipment-open=helmet]')",'Шлем с фиксатором');
    await page.evaluate("document.querySelector('.loadout-slot[data-equipment-open=helmet]').click();true");
    await page.until("document.querySelector('[data-neck-brace-status]')?.textContent.includes('готов')",'Готовность фиксатора');
    await request(loc+'/combat/start',{location_character_ids:[gunner.location_character_id,placement.location_character_id],
        initiator_location_character_id:gunner.location_character_id,initiator_first:true},token);
    const command={location_character_id:gunner.location_character_id,weapon_index:0,target_character_id:owner.id};
    await request(loc+'/combat/action',{...command,action_key:'draw_weapon'},token);
    await request(loc+'/combat/action',{...command,action_key:'aim'},token);
    const state=await request(loc+'/combat',null,token,'GET');
    const cost=state.current_character.weapon_ergonomics.find(profile=>profile.weapon_index===0).aimed_shot_action_points;
    const hit=await request(loc+'/combat/action',{...command,action_key:'attack',fire_mode:'aimed',target_zone:'head',
        shot_count:1,volley_count:1,action_points:cost},token);
    assert.ok(hit.attack.results[0].neck_brace);assert.equal(hit.attack.results[0].damage,0);
    await page.until("document.querySelector('[data-neck-brace-status]')?.textContent.includes('10 мин')",'Фиксатор израсходован по сети');
    await request(loc+'/combat/end',{},token);
    const clock=await request(`/lobbies/${lobby.id}`,null,token,'GET'),absolute=clock.game_day*1440+clock.game_time_minutes+10;
    await request(`/lobbies/${lobby.id}/time`,{game_day:Math.floor(absolute/1440),game_time_minutes:absolute%1440},token,'PATCH');
    await page.until("document.querySelector('[data-neck-brace-status] button')",'После десяти минут доступна проверка');
    await page.evaluate("document.querySelector('[data-neck-brace-status] button').click();true");
    await page.until("document.querySelector('#consumable-application-modal')",'Выбор инженера');
    const choices=await page.evaluate("[...document.querySelectorAll('#consumable-application-modal .consumable-application-list button')].map(button=>button.textContent)");
    assert.ok(choices.length<=4&&choices[0]==='Самостоятельно','Выбираются только близкие управляемые персонажи, а не все 50 НПС');
    await page.evaluate("document.querySelector('#consumable-application-modal .consumable-application-list button').click();true");
    await page.until("!document.querySelector('#consumable-application-modal')&&document.querySelector('[data-neck-brace-status]')?.textContent.includes('готов')",'Фиксатор перезаряжен проверкой');
    const saved=await request('/lobbies/characters/'+owner.id,null,token,'GET');assert.ok(saved.data.equipment.helmet.neckBraceState.charged);
    for(const [width,height]of [[1920,1080],[1600,900],[1366,768],[1280,720]]){
        await page.call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
        await page.until("(()=>{const root=document.getElementById('sheet-tab-equipment').getBoundingClientRect(),status=document.querySelector('[data-neck-brace-status]')?.getBoundingClientRect();return status&&status.y>=root.y&&status.bottom<=root.bottom&&status.right<=root.right})()",'Состояние фиксатора видно '+width);
        await page.until("(()=>{const root=document.getElementById('sheet-tab-equipment').getBoundingClientRect(),description=document.querySelector('[data-equipment-panel=helmet] .modifications-block').getBoundingClientRect();return description.bottom<=root.bottom&&description.right<=root.right})()",'Полное описание фиксатора без прокрутки '+width);
        if(width===1280)fs.writeFileSync(path.join(safeSession.artifacts,'neck-brace-1280x720.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    }
    console.log('Фиксатор шеи: штатная установка, заблокированное непробитие, ожидание 10 игровых минут, выбор близкого инженера и успешная перезарядка через интерфейс; четыре настольных размера.');
    await require('./auto_injector.browser.cjs')({page,request,token,lobby,offered,loc,npc,safeSession});
};
