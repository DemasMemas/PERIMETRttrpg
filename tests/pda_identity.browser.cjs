// Подмена подписи при настоящей установке b21; логин чужого аккаунта не используется для входа.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,loc,locationId,characterId,safeSession})=>{
    await page.evaluate('window.closeCharacterSheet();true');
    const api='/lobbies/'+lobby.id,url='/lobbies/characters/'+characterId,get=()=>request(url,null,token,'GET');
    let fresh=await get();fresh.data.inventory.pockets.push({id:'identity-board',name:'Плата',category:'crafting_material',quantity:2},
        {id:'identity-electronics',name:'Электронные материалы',category:'crafting_material',quantity:4});
    await request(url,{data:fresh.data},token,'PUT');fresh=await get();
    const pdaIndex=fresh.data.inventory.pockets.findIndex(i=>i.id==='artifact-pda'),pdaPath=['inventory','pockets',pdaIndex];
    const job=await request(url+'/crafting',{operation:'install_pda_modification',operation_id:'identity-install',
        character_revision:fresh.data._revision,target_path:pdaPath,item_id:'artifact-pda',rule_id:'pda-b21'},token);
    const clock=await request(api,null,token,'GET'),absolute=clock.game_day*1440+clock.game_time_minutes+60;
    await request(api+'/time',{game_day:Math.floor(absolute/1440),game_time_minutes:absolute%1440},token,'PATCH');
    fresh=await get();const installed=await request(url+'/crafting',{operation:'finish',operation_id:'identity-finish',
        character_revision:fresh.data._revision,job_id:job.result.id,chosen_loss:[]},token);assert.ok(installed.result.success);
    await request(api+'/weather',{sun:{enabled:true,intensity:.7},rain:{enabled:false,intensity:0},fog:{enabled:false,intensity:0},emission:{enabled:false,intensity:0}},token,'PATCH');
    const receiver=await request(api+'/characters',{name:'Получатель подменённого имени',data:{inventory:{pockets:[{
        id:'identity-receiver-pda',name:'КПК',quantity:1,category:'misc'}]},health:{current:700,max:700,effects:[]}}},token);
    await request(loc+'/spawn_character',{character_id:receiver.id,tile_x:44,tile_y:49},token);
    const visibleName='echo-identity';await request('/lobbies/characters/'+receiver.id+'/pda-account/register',{
        item_path:['inventory','pockets',0],item_id:'identity-receiver-pda',login:visibleName,password:'synthetic-identity-secret'},token);
    fresh=await get();const accountBefore=fresh.data.inventory.pockets[pdaIndex].pdaNetwork.account_id;
    await request(url+'/portable-lights',{operation:'pda_hold',path:pdaPath,source_id:'artifact-pda',character_revision:fresh.data._revision},token);
    const people=await request(loc+'/combat',null,token,'GET'),actor=people.characters.find(c=>c.character_id===characterId);
    await request(loc+'/combat/start',{location_character_ids:[actor.location_character_id],initiator_location_character_id:actor.location_character_id,initiator_first:true},token);
    const points=(await request(loc+'/combat',null,token,'GET')).current_character.action_points_current;
    const open=async(id,deviceId)=>{
        await page.evaluate(`document.querySelector('[data-pda-close]')?.click();window.closeCharacterSheet();window.currentLocationCharacterId=${id};window.currentCharacterId=${id};window.openCharacterSheet(${id},'inventory').then(()=>true)`);
        await page.until(`Boolean([...document.querySelectorAll('[data-widget-identity]')].find(e=>JSON.parse(e.dataset.widgetIdentity).id===${JSON.stringify(deviceId)}))`,'Физический КПК для подмены имени');
        await page.evaluate(`[...document.querySelectorAll('[data-widget-identity]')].find(e=>JSON.parse(e.dataset.widgetIdentity).id===${JSON.stringify(deviceId)}).querySelector('[data-widget-key=use]').click();true`);
        await page.until("document.querySelector('.pda-shell-nav')",'КПК открыт');
    };
    await open(characterId,'artifact-pda');
    const contacts=()=>page.evaluate("[...document.querySelectorAll('.pda-shell-nav button')].find(b=>b.textContent==='Контакты').click();true");
    await contacts();await page.until("!document.querySelector('[data-radio-identity]').hidden&&!document.querySelector('[data-radio-alias] input').disabled",'Форма изменения имени доступна');
    const saved=async name=>{for(let i=0;i<50;i++){const data=await get();if((data.data.inventory.pockets[pdaIndex].pdaNetwork.sender_name||'')===name)return data;await new Promise(resolve=>setTimeout(resolve,100));}assert.fail('Имя не сохранено');};
    const setName=async name=>{
        await contacts();await page.until("!document.querySelector('[data-radio-identity]').hidden&&!document.querySelector('[data-radio-alias] input').disabled",'Настройка имени загружена');
        await page.evaluate(`(()=>{const input=document.querySelector('[data-radio-alias] input');input.value=${JSON.stringify(name)};input.dispatchEvent(new Event('input'));document.querySelector('[data-radio-alias] button').click();return true})()`);
        await saved(name);
    };
    await setName(visibleName);
    assert.equal((await request(loc+'/combat',null,token,'GET')).current_character.action_points_current,points-3);
    const aligned=await page.evaluate("(()=>{const i=document.querySelector('[data-radio-alias] input').getBoundingClientRect(),b=document.querySelector('[data-radio-alias] button').getBoundingClientRect();return Math.abs(i.height-b.height)<1&&Math.abs(i.bottom-b.bottom)<1})()");assert.ok(aligned);
    fs.writeFileSync(path.join(safeSession.artifacts,'pda-identity.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    const inbox=()=>request('/lobbies/characters/'+receiver.id+'/pda-network?'+new URLSearchParams({item_path:JSON.stringify(['inventory','pockets',0]),item_id:'identity-receiver-pda',location_id:locationId}),null,token,'GET');
    const transmit=async text=>{
        await page.evaluate("[...document.querySelectorAll('.pda-shell-nav button')].find(b=>b.textContent==='Общий чат').click();true");
        await page.until("Boolean([...document.querySelector('[data-radio-send] select').options].find(o=>o.textContent==='echo-identity'&&!o.value.startsWith('device:')))",'Получатель обычного сообщения');
        await page.evaluate(`(()=>{const select=document.querySelector('[data-radio-send] select');select.value=[...select.options].find(o=>o.textContent==='echo-identity'&&!o.value.startsWith('device:')).value;select.dispatchEvent(new Event('change'));const input=document.querySelector('[data-radio-send] textarea');input.value=${JSON.stringify(text)};input.dispatchEvent(new Event('input'));document.querySelector('[data-radio-send] button').click();return true})()`);
        for(let i=0;i<50;i++){const view=await inbox();if(view.messages.some(m=>m.text===text))return view;await new Promise(resolve=>setTimeout(resolve,100));}assert.fail('Сообщение не доставлено');
    };
    const first=await transmit('Проверка подмены имени');assert.equal(first.messages.find(m=>m.text==='Проверка подмены имени').sender,visibleName);
    await open(receiver.id,'identity-receiver-pda');await contacts();
    await page.until("[...document.querySelectorAll('[data-radio-signals] .pda-radio-contact')].some(r=>r.textContent.startsWith('echo-identity')&&r.querySelector('button'))",'Сигнал с чужим видимым именем');
    await page.evaluate("[...document.querySelectorAll('[data-radio-signals] .pda-radio-contact')].find(r=>r.textContent.startsWith('echo-identity')).querySelector('button').click();true");
    let friend=null;for(let i=0;i<50;i++){friend=(await inbox()).contacts.find(c=>c.account_id===accountBefore);if(friend)break;await new Promise(resolve=>setTimeout(resolve,100));}
    assert.ok(friend);assert.equal(friend.login,visibleName);
    await page.until("document.querySelector('[data-radio-saved]').innerText.includes('echo-identity')&&!document.querySelector('[data-radio-saved]').innerText.includes('spam-origin')",'Контакт связан с настоящим отправителем без раскрытия логина');
    await request(loc+'/combat/end',{},token);await open(characterId,'artifact-pda');await setName('');
    const final=await transmit('Проверка настоящего имени');
    assert.equal(final.messages.find(m=>m.text==='Проверка подмены имени').sender,visibleName);
    assert.equal(final.messages.find(m=>m.text==='Проверка настоящего имени').sender,'spam-origin');
    assert.equal((await get()).data.inventory.pockets[pdaIndex].pdaNetwork.account_id,accountBefore);
    fs.writeFileSync(path.join(safeSession.artifacts,'pda-identity.json'),JSON.stringify({scene:'Припять',installedViaCrafting:'pda-b21',
        existingLoginAsAlias:true,passwordNotRequested:true,realAccountUnchanged:true,paidActionPoints:3,historyAfterReset:true,formAligned:true,contactUsesRealId:true,contactKeepsObservedName:true},null,2));
    console.log('Замена данных: настоящая установка b21, существующий логин без пароля,3ОД, неизменный аккаунт, история после сброса; форма выровнена.');
};
