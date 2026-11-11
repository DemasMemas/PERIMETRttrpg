// Крепление света: штатная установка, свободные руки, луч и горящий ХИС.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,offered,loc,npc,safeSession})=>{
    assert.ok(['catalog','equipment'].includes(process.env.VTT_SAFE_SCENARIO));assert.ok(safeSession.verified);
    const evidence=process.env.VTT_SAFE_SCENARIO==='equipment'?require('./helpers/playtest_registry.cjs').evidenceWriter(safeSession,'belt-light'):null;
    await page.call('Emulation.setDeviceMetricsOverride',{width:1920,height:1080,deviceScaleFactor:1,mobile:false});
    const item=(t,id)=>({id,templateId:t.id,name:t.name,category:t.category,attributes:structuredClone(t.attributes||{}),quantity:1,weight:t.weight,volume:t.volume});
    const belt=item(offered.global.find(t=>t.category==='belt'),'light-browser-belt');
    const lamp=item(offered.global.find(t=>t.category==='misc'&&t.name==='Фонарик'),'belt-browser-lamp');
    const his=item(offered.global.find(t=>t.name==='ХИС'),'belt-browser-his');
    const owner=await request(`/lobbies/${lobby.id}/characters`,{name:'Испытатель света на поясе',data:{equipment:{belt},
        weapons:[{id:'belt-rifle',name:'Проверочная винтовка',category:'weapon',ergonomics:100,weight:1,volume:6,ammo:1,damage:10,range:20}],
        inventory:{money:100000,pockets:[lamp,his],backpack:[]}}},token);
    const placed=await request(loc+'/spawn_character',{character_id:owner.id,tile_x:47,tile_y:49},token);
    const master=await request('/lobbies/characters/'+npc.id,null,token,'GET');
    await request(loc+'/professional-modification',{actor_location_character_id:placed.location_character_id,specialist_character_id:npc.id,
        target_path:['equipment','belt'],item_id:belt.id,rule_id:'armor-g62',execute:true,operation_id:'belt-light-install',
        actor_revision:owner.data._revision,specialist_revision:master.data._revision},token);
    await request(loc+'/spawn_character',{character_id:owner.id,tile_x:49,tile_y:50},token);
    await request(loc+'/characters/'+owner.id+'/facing',{facing_x:0,facing_y:1},token,'PATCH');
    const detail=await request(loc,null,token,'GET');await request(loc,{lighting:{...detail.lighting,mode:'manual',ambient_intensity:.15,directional_intensity:.1}},token,'PUT');
    await page.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');window.T=await import('three');window.mapAudit=(await import('/static/js/mapSceneContext.js')).getMapSceneContext();
        locationControls.enableDamping=false;locationControls.target.set(49.5,1,52);locationControls.object.position.set(54,7,48);locationControls.update();return true})()`);
    const selectPanel=key=>page.evaluate(`document.querySelector('.equipment-panel-tab[data-panel=${key}]').click();true`);
    const open=async()=>{
        await page.evaluate(`window.openCharacterSheet(${owner.id},'equipment').then(()=>true)`);
        await page.until("document.querySelector('.equipment-panel-tab[data-panel=detector]')",'Вкладка приборов');
        await selectPanel('detector');
    };
    const row=id=>`document.querySelector('.portable-light-row[data-light-id="${id}"]')`;
    await open();await page.until(row(lamp.id),'Фонарь в экипировке');
    await page.evaluate(`${row(lamp.id)}.querySelector('[data-light-operation=prepare]').click();true`);
    await page.until(`${row(lamp.id)}?.textContent.includes('в руке')`,'Фонарь в руке');
    await page.evaluate(`${row(lamp.id)}.querySelector('[data-light-operation=toggle]').click();true`);
    await page.until(`${row(lamp.id)}?.textContent.includes('включён')`,'Фонарь включён');
    await page.evaluate('window.closeCharacterSheet();true');
    const shot=async name=>fs.writeFileSync(path.join(safeSession.artifacts,name),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    await shot('belt-light-before-hand.png');
    await request(loc+'/combat/start',{location_character_ids:[placed.location_character_id],initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
    const before=await request(loc+'/combat',null,token,'GET');await open();
    await page.until(`${row(lamp.id)}?.textContent.includes('На пояс')`,'Крепление с ценой');
    await page.evaluate(`[...${row(lamp.id)}.querySelectorAll('button')].find(b=>b.textContent.startsWith('На пояс')).click();true`);
    await page.until(`${row(lamp.id)}?.textContent.includes('на поясе')`,'Свет на поясе');
    const after=await request(loc+'/combat',null,token,'GET');
    assert.equal(after.current_character.action_points_current,before.current_character.action_points_current-3);
    assert.equal(after.current_character.portable_lights.find(s=>s.id===lamp.id).mount,'belt');
    evidence?.step('Включённый фонарь перенесён из руки на пояс',
        {mount:'hand',actionPoints:before.current_character.action_points_current},
        {mount:after.current_character.portable_lights.find(s=>s.id===lamp.id).mount,actionPoints:after.current_character.action_points_current});
    await request(loc+'/combat/action',{location_character_id:placed.location_character_id,action_key:'draw_weapon',weapon_index:0},token);
    const saved=await request('/lobbies/characters/'+owner.id,null,token,'GET');
    assert.notEqual(saved.data.health.combatMeta.weaponGripHands,1,'После крепления доступны обе руки');
    const armed=await request(loc+'/combat',null,token,'GET');assert.equal(armed.current_character.weapon_grip_hands,2);
    evidence?.step('Оружие после крепления фонаря', {occupiedHand:'Фонарь'},
        {weapon:saved.data.weapons[0].name,gripHands:armed.current_character.weapon_grip_hands});
    await page.evaluate('window.closeCharacterSheet();true');
    await page.until("mapAudit.scene.children.some(light=>light.isSpotLight&&Math.abs(light.position.x-49.5)<.1&&Math.abs(light.position.z-50.85)<.1)",'Настоящий луч у персонажа');
    const visualHeight=await page.evaluate("mapAudit.scene.children.find(light=>light.isSpotLight&&Math.abs(light.position.x-49.5)<.1&&Math.abs(light.position.z-50.85)<.1).position.y");
    assert.ok(Math.abs(visualHeight-after.current_character.portable_lights.find(s=>s.id===lamp.id).y)<.1,'Высота игрового и видимого света совпадает');
    await shot('belt-light-after-belt.png');
    await page.evaluate('locationControls.object.position.set(46,6,49);locationControls.update();true');
    await shot('belt-light-other-angle.png');
    await request(loc+'/combat/action',{location_character_id:placed.location_character_id,action_key:'stow_weapon'},token);
    await request(loc+'/combat/end',{},token);await open();
    await page.until(`${row(lamp.id)}?.querySelector('[data-light-operation=stow]:not(:disabled)')`,'Доступно снятие фонаря с пояса');
    await page.evaluate(`${row(lamp.id)}.querySelector('[data-light-operation=stow]').click();true`);
    await page.until(`${row(lamp.id)}?.querySelector('[data-light-operation=prepare]:not(:disabled)')
        && ${row(his.id)}?.querySelector('[data-light-operation=ignite]:not(:disabled)')`,'Крепление освобождено');
    await page.evaluate(`${row(his.id)}.querySelector('[data-light-operation=ignite]').click();true`);
    await page.until(`${row(his.id)}?.textContent.includes('30 мин')`,'ХИС активирован');
    await page.evaluate(`[...${row(his.id)}.querySelectorAll('button')].find(b=>b.textContent.startsWith('На пояс')).click();true`);
    await page.until(`${row(his.id)}?.textContent.includes('на поясе')`,'ХИС на поясе');
    const status=await request('/lobbies/characters/'+owner.id+'/portable-lights',null,token,'GET');
    assert.equal(status.lights.find(light=>light.id===his.id).remaining_seconds,1800);
    evidence?.step('На свободное крепление помещён активный ХИС', {durationSeconds:1800},
        {remainingSeconds:status.lights.find(light=>light.id===his.id).remaining_seconds,mount:status.lights.find(light=>light.id===his.id).mount});
    for(const[width,height]of [[1920,1080],[1600,900],[1366,768],[1280,720]]){
        await page.call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
        await selectPanel('detector');
        await page.until(`(()=>{const root=document.getElementById('sheet-tab-equipment').getBoundingClientRect();
            const rows=[...document.querySelectorAll('.portable-light-row')].filter(row=>['${lamp.id}','${his.id}'].includes(row.dataset.lightId));
            return rows.length===2&&rows.every(row=>{const r=row.getBoundingClientRect();return r.width>0&&r.y>=root.y&&r.bottom<=root.bottom&&r.right<=root.right})})()`,'Оба источника видны '+width);
        if(width===1280)await shot('belt-his-1280x720.png');
        await page.evaluate("document.querySelector('.loadout-slot[data-equipment-open=weapon--1]').click();true");
        await page.until("(()=>{const grid=document.querySelector('[data-weapon-index=\"-1\"] .weapon-summary-grid');return grid?.getBoundingClientRect().width>0&&[...grid.children].every(cell=>{const label=cell.querySelector('strong').getBoundingClientRect(),r=cell.getBoundingClientRect();return label.right<=r.right+.5})})()",'Характеристики кулаков не перекрываются '+width);
    }
    console.log('Свет на поясе: штатная установка g62, перенос включённого фонаря из руки за 3 ОД, обе руки доступны оружию, настоящий луч и два ракурса Припяти, ХИС без сброса таймера, четыре настольных размера.');
    evidence?.publish({images:['belt-light-before-hand.png','belt-light-after-belt.png','belt-light-other-angle.png','belt-his-1280x720.png'],
        controls:{random:'Бросков в этом сценарии нет',time:'ХИС активируется на 1800 игровых секунд; ход времени не ускоряется'},
        limitations:['Высота луча проверена стоя; сидя и лёжа отдельно визуально не проверялись.',
            'Один клиент ГМа. Права отдельного игрока этим случаем не подтверждаются.']});
};
