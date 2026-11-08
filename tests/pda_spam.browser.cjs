// Установка двух модификаций и пять настоящих раундов; звук не зависит от детектора приближения.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,loc,characterId,locationId,safeSession})=>{
    await page.call('Emulation.setDeviceMetricsOverride',{width:1920,height:1080,deviceScaleFactor:1,mobile:false});
    const api='/lobbies/'+lobby.id,url='/lobbies/characters/'+characterId,get=id=>request('/lobbies/characters/'+id,null,token,'GET');
    await request(loc+'/objects',{name:'Станок Электрика',type:'workbench',tile_x:43,tile_y:48,
        properties:{craft_workbench:'Станок Электрика'}},token);
    let source=await get(characterId);source.data.features={specialTraits:[{name:'Программист'}]};
    source.data.skills ||= {};source.data.skills.other ||= {};source.data.skills.other.engineering={base:100};source.data.weapons=[];
    source.data.inventory.pockets.push({id:'spam-board',name:'Плата',category:'crafting_material',quantity:1},
        {id:'spam-electronics',name:'Электронные материалы',category:'crafting_material',quantity:1});
    await request(url,{data:source.data},token,'PUT');
    const receiver=await request(api+'/characters',{name:'Получатель спам-атаки',data:{features:{specialTraits:[{name:'Программист'}]},
        skills:{other:{engineering:{base:100}}},inventory:{pockets:[{id:'spam-receiver-pda',name:'КПК',category:'misc',quantity:1},
            {name:'Электронные материалы',category:'crafting_material',quantity:2},
            {id:'spam-solder',name:'Паяльные инструменты',category:'tool',quantity:1,uses:2,maxUses:50}],backpack:[]},health:{current:700,max:700,effects:[]}}},token);
    const placed=await request(loc+'/spawn_character',{character_id:receiver.id,tile_x:46,tile_y:48},token);
    const begin=async(id,index,rule,toolPath)=>{const fresh=await get(id);return request('/lobbies/characters/'+id+'/crafting',{
        operation:'install_pda_modification',operation_id:'install-'+rule,character_revision:fresh.data._revision,
        target_path:['inventory','pockets',index],item_id:fresh.data.inventory.pockets[index].id,rule_id:rule,...(toolPath?{tool_path:toolPath}:{})},token);};
    const attackJob=await begin(characterId,1,'pda-b17'),detectorJob=await begin(receiver.id,0,'pda-b8',['inventory','pockets',2]);
    const clock=await request(api,null,token,'GET'),absolute=clock.game_day*1440+clock.game_time_minutes+60;
    await request(api+'/time',{game_day:Math.floor(absolute/1440),game_time_minutes:absolute%1440},token,'PATCH');
    await request(api+'/weather',{sun:{enabled:true,intensity:.7},rain:{enabled:false,intensity:0},fog:{enabled:false,intensity:0},emission:{enabled:false,intensity:0}},token,'PATCH');
    for(const [id,job]of [[characterId,attackJob],[receiver.id,detectorJob]]){
        const fresh=await get(id),done=await request('/lobbies/characters/'+id+'/crafting',{operation:'finish',operation_id:'finish-spam-'+id,
            character_revision:fresh.data._revision,job_id:job.result.id,chosen_loss:[]},token);assert.ok(done.result.success);
    }
    for(const [id,index,login]of [[characterId,1,'spam-origin'],[receiver.id,0,'spam-recipient']]){
        const fresh=await get(id);await request('/lobbies/characters/'+id+'/pda-account/register',{
            item_path:['inventory','pockets',index],item_id:fresh.data.inventory.pockets[index].id,login,password:'synthetic-spam-password'},token);
    }
    let target=await get(receiver.id);
    await request('/lobbies/characters/'+receiver.id+'/pda-network',{operation:'proximity_sound',enabled:false,
        item_path:['inventory','pockets',0],item_id:'spam-receiver-pda',location_id:locationId,character_revision:target.data._revision},token);
    const hardware=async(operation,index)=>{const fresh=await get(characterId);return request(url+'/portable-lights',{
        operation,path:['inventory','pockets',index],source_id:fresh.data.inventory.pockets[index].id,character_revision:fresh.data._revision},token);};
    await hardware('stow',0);await hardware('pda_hold',1);
    const models=await request(loc+'/combat',null,token,'GET'),actor=models.characters.find(c=>c.character_id===characterId);
    await request(loc+'/combat/start',{location_character_ids:[actor.location_character_id,placed.location_character_id],
        initiator_location_character_id:actor.location_character_id,initiator_first:true},token);
    const before=await request(loc+'/combat',null,token,'GET'),points=before.current_character.action_points_current;
    await page.evaluate(`window.currentLocationCharacterId=${receiver.id};window.currentCharacterId=${receiver.id};window.__spamSounds=[];window.addEventListener('pda-spam-sound',e=>__spamSounds.push(e.detail.message_id));true`);
    const open=async(id,index)=>{
        await page.evaluate(`window.openCharacterSheet(${id},'inventory').then(()=>true)`);
        const fresh=await get(id),identity=fresh.data.inventory.pockets[index].id;
        await page.until(`Boolean([...document.querySelectorAll('[data-widget-identity]')].find(e=>JSON.parse(e.dataset.widgetIdentity).id===${JSON.stringify(identity)}))`,'Физический КПК в инвентаре');
        await page.evaluate(`[...document.querySelectorAll('[data-widget-identity]')].find(e=>JSON.parse(e.dataset.widgetIdentity).id===${JSON.stringify(identity)}).querySelector('[data-widget-key=use]').click();true`);
        await page.until("document.getElementById('pda-shell-modal')",'КПК открыт');
        await page.evaluate("[...document.querySelectorAll('#pda-shell-modal nav button')].find(b=>b.textContent==='Общий чат').click();true");
    };
    await open(characterId,1);
    await page.until("!document.querySelector('[data-radio-spam]').hidden&&[...document.querySelector('[data-radio-send] select').options].some(o=>o.value.startsWith('device:'))",'Установленная спам-атака доступна');
    const text='<b>Контрольный сигнал</b>';
    await page.evaluate(`(()=>{const input=document.querySelector('[data-radio-send] textarea');input.value=${JSON.stringify(text)};input.dispatchEvent(new Event('input',{bubbles:true}));const select=document.querySelector('[data-radio-send] select');select.value=[...select.options].find(o=>o.textContent.includes('spam-recipient')&&o.value.startsWith('device:')).value;select.dispatchEvent(new Event('change'));return true})()`);
    const point=await page.evaluate("(()=>{const b=document.querySelector('[data-radio-spam-start]');b.scrollIntoView({block:'center'});const r=b.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()");
    for(const type of ['mouseMoved','mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{type,...point,button:type==='mouseMoved'?'none':'left',buttons:type==='mousePressed'?1:0,clickCount:type==='mouseMoved'?0:1});
    await page.until("document.querySelector('[data-radio-spam-status]')?.textContent.includes('Осталось сигналов: 5')",'Атака запущена один раз');
    const paid=await request(loc+'/combat',null,token,'GET');assert.equal(paid.current_character.action_points_current,points-3);
    await page.evaluate("document.querySelector('[data-pda-close]').click();window.closeCharacterSheet();true");await open(receiver.id,0);
    for(let count=1;count<=5;count++){
        await request(loc+'/combat/end_turn',{location_character_id:actor.location_character_id},token);
        assert.equal(((await get(receiver.id)).data.inventory.pockets[0].pdaNetwork.spam_messages||[]).length,count-1);
        await request(loc+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
        await page.until(`__spamSounds.length===${count}`,'Обязательный звуковой сигнал '+count);
        await page.until(`document.querySelectorAll('[data-radio-messages] .pda-radio-message').length===${count}`,'Сообщение в физическом КПК '+count);
    }
    assert.equal(await page.evaluate("document.querySelectorAll('[data-radio-messages] b').length"),0,'Текст не исполняется как HTML');
    target=await get(receiver.id);assert.equal(target.data.inventory.pockets[0].pdaNetwork.proximity_sound,false);
    assert.equal(new Set(await page.evaluate('__spamSounds')).size,5);
    await request(loc+'/combat/end',{},token);
    fs.writeFileSync(path.join(safeSession.artifacts,'pda-spam.json'),JSON.stringify({scene:'Припять',roles:'ГМ с двумя своими персонажами',
        installedViaCrafting:['pda-b17','pda-b8'],paidActionPoints:3,rounds:5,messages:5,actualSoundStarts:5,proximitySound:false,textSafe:true},null,2));
    fs.writeFileSync(path.join(safeSession.artifacts,'pda-spam.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    console.log('Спам-атака: настоящая установка модификации,3ОД,5раундов/5сообщений/5звуков,отключённый детектор не приглушает атаку.');
};
