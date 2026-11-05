// Согласие обычного игрока во втором независимом браузерном контексте.
const assert=require('node:assert/strict');
const {waitForDocument}=require('./durable_reload.browser.cjs');
module.exports=async({page,request,token,lobby,location,actor,base,connect,debugPort,safeSession})=>{
    assert.ok(safeSession.verified&&process.env.VTT_SAFE_SCENARIO==='trade');
    const api=`/lobbies/${lobby.id}`,loc=api+'/locations/'+location.id;
    await request('/auth/register',{username:'trade-player',email:'trade-player@example.test',password:'synthetic-trade-password'});
    const player=await request('/auth/login',{username:'trade-player',password:'synthetic-trade-password'});
    await request(api+'/join',{},player.access_token);
    const recipient=await request(api+'/characters',{name:'Получатель игрока',data:{inventory:{money:0,pockets:[],backpack:[]},
        skills:{physical:{strength:{base:10},shooting:{base:10},awareness:{base:10}}},
        health:{current:700,max:700,effects:[]}}},player.access_token);
    await request(loc+'/spawn_character',{character_id:recipient.id,tile_x:40,tile_y:48,assign_to_user_id:player.user_id},token);
    const version=await(await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();
    let protocol,context,peer;
    try{
        protocol=await connect(version.webSocketDebuggerUrl);
        context=await protocol.call('Target.createBrowserContext');
        const tab=await protocol.call('Target.createTarget',{url:base,browserContextId:context.browserContextId});
        const info=(await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json()).find(entry=>entry.id===tab.targetId);
        peer=await connect(info.webSocketDebuggerUrl);await peer.call('Page.enable');await peer.call('Runtime.enable');
        await waitForDocument(peer,{href:base,label:'Отдельный игрок обмена'});
        await peer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(player.access_token)});localStorage.setItem('user_id',${JSON.stringify(String(player.user_id))});true`);
        const href=base+api+'/page?location='+location.id,epoch=await peer.evaluate('performance.timeOrigin');
        await peer.call('Page.navigate',{url:href});await waitForDocument(peer,{href,afterEpoch:epoch,label:'Игрок на копии Припяти'});
        await peer.until(`window.socket?.connected&&window.isGM===false&&window.currentLocationId===${location.id}&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Обычный игрок подключён');
        await peer.evaluate(`window.__peerTradeEvents=[];socket.on('character_data_updated',event=>window.__peerTradeEvents.push(event));true`);
        await peer.evaluate(`window.openCharacterSheet(${recipient.id},'inventory').then(()=>true)`);
        await peer.until(`document.querySelector('#character-sheet-modal')?.style.display==='flex'`,'Лист получателя открыт и подписан на обновления');
        await page.evaluate(`import('/static/js/locationScene.js').then(module=>module.showCharacterTradeMenu(${actor.placed.location_character_id},${actor.character.id},${recipient.id})).then(()=>true)`);
        await page.evaluate(`(()=>{const money=document.querySelector('[data-trade-side="actor"] .trade-money');money.value='125';money.dispatchEvent(new Event('input'));document.querySelector('.trade-confirm').click();return true})()`);
        await peer.until(`document.querySelector('.interaction-accept')`,'Игрок получил предложение через событие');
        assert.equal((await request('/lobbies/characters/'+recipient.id,null,player.access_token,'GET')).data.inventory.money,0,'До согласия деньги не поступили');
        await peer.evaluate(`document.querySelector('.interaction-accept').click();true`);
        await peer.until(`window.__peerTradeEvents.some(event=>event.character_id===${recipient.id}&&event.updates?.data?.inventory?.money===125)`,'Игрок получил новое состояние денег');
        assert.equal((await request('/lobbies/characters/'+recipient.id,null,player.access_token,'GET')).data.inventory.money,125);
        const beforeReload=await peer.evaluate('performance.timeOrigin');
        await peer.call('Page.reload');await waitForDocument(peer,{href,afterEpoch:beforeReload,label:'Перезагрузка игрока после обмена'});
        await peer.until(`window.socket?.connected&&window.currentLocationId===${location.id}`,'Переподключение после обмена');
        assert.equal((await request('/lobbies/characters/'+recipient.id,null,player.access_token,'GET')).data.inventory.money,125);
        console.log(JSON.stringify({trade_player_consent:true,second_browser_context:true,real_time_money_update:true,reload:true}));
        await peer.evaluate(`window.closeCharacterSheet?.();true`);
        const [devices,headphones,helmets]=await Promise.all(['device','headphones','helmet'].map(category=>request(api+'/templates?category='+category,null,token,'GET')));
        const nvg=devices.global.find(template=>template.name==='Очки ПНВ-1п');
        const phones=headphones.global.find(template=>template.attributes?.awareness_bonus>0);
        const helmet=helmets.global[0];assert.ok(nvg&&phones&&helmet);
        const data=(await request('/lobbies/characters/'+recipient.id,null,token,'GET')).data;
        data.equipment={helmet:{id:'peer-helmet',name:helmet.name,templateId:helmet.id,attributes:helmet.attributes,
            installedModules:[{id:'peer-nvg',slotType:'nvg',name:nvg.name,templateId:nvg.id,attributes:nvg.attributes}]},
            headphones:{id:'peer-headphones',name:phones.name,templateId:phones.id,attributes:phones.attributes}};
        await request('/lobbies/characters/'+recipient.id,{data},token,'PUT');
        await request(loc+'/spawn_character',{character_id:recipient.id,tile_x:0,tile_y:0,force_relocate:true},token);
        await request(api+'/time',{game_day:1,game_time_minutes:0},token,'PATCH');
        await request(api+'/weather',{sun:{enabled:true,intensity:.5},rain:{enabled:false,intensity:0},fog:{enabled:true,intensity:.5}},token,'PATCH');
        await peer.until(`window.lobbyGameTimeMinutes===0&&window.weatherSettings?.fog?.enabled`,'У игрока актуальны время и погода');
        await peer.evaluate(`window.openCharacterSheet(${recipient.id},'skills').then(()=>true)`);
        await peer.until(`document.querySelector('[name="skills.physical.shooting.base"]')?.value==='10'`,'Навыки обычного игрока открыты');
        const read=()=>request('/lobbies/characters/'+recipient.id,null,player.access_token,'GET');
        const last=sheet=>sheet.data.health.combatMeta.socialCheckReceipts.at(-1).result.check;
        const clickSkill=path=>peer.evaluate(`(()=>{const element=[...document.querySelectorAll('#sheet-tab-skills [onclick]')].find(e=>e.getAttribute('onclick').includes(${JSON.stringify("window.rollSkill('"+path+"'")}));if(!element)throw Error('Нет кнопки навыка');element.click();return true})()`);
        const receiptCount=async expected=>peer.until(`(async()=>{const {Server}=await import('/static/js/api.js');const c=await Server.getCharacter(${recipient.id});return c.data.health?.combatMeta?.socialCheckReceipts?.length===${expected}})()`,'Бросок сохранён сервером');
        await clickSkill('physical.shooting');await receiptCount(1);
        let check=last(await read());
        assert.equal(check.weather_modifier,-2);assert.equal(check.equipment_modifier,-2);
        assert.equal(check.difficulty,null);assert.equal(check.success,null);assert.equal(check.disadvantage,false);
        for(const [index,weather,equipment] of [[0,-3,-2],[1,-2,phones.attributes.awareness_bonus]]){
            await clickSkill('physical.awareness');
            await peer.until(`document.querySelectorAll('#consumable-application-modal .consumable-application-list button').length===2`,'Сохранён выбор зрения или слуха');
            await peer.evaluate(`document.querySelectorAll('#consumable-application-modal .consumable-application-list button')[${index}].click();true`);
            await receiptCount(index+2);check=last(await read());
            assert.equal(check.weather_modifier,weather);assert.equal(check.equipment_modifier,equipment);
        }
        await peer.until(`[...document.querySelectorAll('.notification')].some(element=>element.textContent.includes('Внимательность (слух)')&&element.textContent.includes('Погода на улице'))`,'Игрок видит погодный модификатор в результате');
        console.log(JSON.stringify({quick_sheet_checks:true,normal_player:true,weather:true,nvg:true,hearing_headphones:true,server_receipts:3,no_invented_dc:true}));
        await peer.evaluate(`window.closeCharacterSheet?.();true`);
        const observer=(await read()).data;observer.equipment.helmet.installedModules=[];
        await request('/lobbies/characters/'+recipient.id,{data:observer},token,'PUT');
        const sourceId=actor.character.id,source=(await request('/lobbies/characters/'+sourceId,null,token,'GET')).data;
        source.inventory.pockets.push({id:'peer-light',name:'Фонарик',category:'misc',quantity:1,attributes:{portable_light:'hand'}});
        await request('/lobbies/characters/'+sourceId,{data:source},token,'PUT');
        await request(loc+'/spawn_character',{character_id:sourceId,tile_x:10,tile_y:0,force_relocate:true},token);
        await request(loc+'/characters/'+sourceId+'/facing',{facing_x:-1,facing_y:0},token,'PATCH');
        for(const operation of ['prepare','toggle']){
            const fresh=await request('/lobbies/characters/'+sourceId,null,token,'GET');
            const index=fresh.data.inventory.pockets.findIndex(item=>item.id==='peer-light');
            await request('/lobbies/characters/'+sourceId+'/portable-lights',{operation,path:['inventory','pockets',index],source_id:'peer-light',character_revision:fresh.data._revision},token);
        }
        await peer.evaluate(`window.openCharacterSheet(${recipient.id},'skills').then(()=>true)`);
        await peer.until(`document.querySelector('[name="skills.physical.shooting.base"]')?.value==='10'`,'Лист без ПНВ снова открыт');
        await clickSkill('physical.shooting');await receiptCount(4);check=last(await read());
        assert.equal(check.equipment_modifier,0);assert.equal(check.illumination_modifier,-3);
        assert.equal(check.weather_modifier,-2);assert.equal(check.disadvantage,false);
        await peer.until(`[...document.querySelectorAll('.notification')].some(element=>element.textContent.includes('Переносной свет: -3'))`,'Остаточный штраф обычного фонаря показан');
        console.log(JSON.stringify({portable_light_quick_roll:true,night_disadvantage_removed:true,residual_penalty:-3,weather_preserved:true}));
        await require('./equipment_care.browser.cjs')({peer,request,recipient,token,api,read,devices,nvg,helmet});
        await require('./crafting.browser.cjs')({peer,page,request,recipient,token,api,read});
        await require('./modifications.browser.cjs')({peer,request,recipient,token,api,read});
        await require('./creation_systems.browser.cjs')({peer,request,recipient,token,api,read});
    }finally{
        await peer?.close();
        if(context)await protocol?.call('Target.disposeBrowserContext',{browserContextId:context.browserContextId});
        await protocol?.close();
    }
};
