// Два настоящих клиента: установка утилит, дешифровка, управление и отзыв доступа.
const assert=require('node:assert/strict');
module.exports=async({page,peer,request,token,playerToken,actor,player,lobby,location,loc,api,open,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'electronics');assert.ok(safeSession.verified);
    await request(loc+'/objects',{name:'Станок Электрика',type:'household_workbench',tile_x:42,tile_y:48,
        properties:{craft_workbench:'Станок Электрика'}},token);
    for(const [character,auth,device,rules]of [[actor,token,'dynamo-device',['pda-b26','pda-b18','pda-b25','pda-b5']],[player,playerToken,'radio-player',['pda-b24','pda-b5']]]){
        let fresh=await request('/lobbies/characters/'+character.id,null,auth,'GET');
        fresh.data.features={...fresh.data.features,specialTraits:[{name:'Программист'}]};fresh.data.skills={other:{engineering:{base:100}}};
        fresh.data.inventory.pockets.push({name:'Электронные материалы',category:'crafting_material',quantity:30},{name:'Плата',category:'crafting_material',quantity:20});
        await request('/lobbies/characters/'+character.id,{data:fresh.data},token,'PUT');
        for(const rule of rules){
            fresh=await request('/lobbies/characters/'+character.id,null,auth,'GET');const index=fresh.data.inventory.pockets.findIndex(i=>i.id===device);
            const craft='/lobbies/characters/'+character.id+'/crafting';
            const job=await request(craft,{operation:'install_pda_modification',operation_id:rule+'-install',character_revision:fresh.data._revision,
                target_path:['inventory','pockets',index],item_id:device,rule_id:rule},auth);
            const clock=await request(api,null,token,'GET'),absolute=clock.game_day*1440+clock.game_time_minutes+60;
            await request(api+'/time',{game_day:Math.floor(absolute/1440),game_time_minutes:absolute%1440},token,'PATCH');
            fresh=await request('/lobbies/characters/'+character.id,null,auth,'GET');
            const finished=await request(craft,{operation:'finish',operation_id:rule+'-finish',character_revision:fresh.data._revision,job_id:job.result.id,chosen_loss:[]},auth);
            assert.ok(finished.result.success);
        }
    }
    const bookTemplate=await request(api+'/templates',{name:'Учебник. Раздел: Сила',category:'misc',weight:0,volume:0,
        attributes:{book_kind:'skill',book_subject:'Физические',book_skill_path:'physical.strength',reading_seconds:14400}},token);
    let carrier=await request('/lobbies/characters/'+player.id,null,playerToken,'GET');
    carrier.data.inventory.pockets.push({id:'browser-one-book',name:bookTemplate.name,category:'misc',quantity:1,templateId:bookTemplate.id+1000000,attributes:bookTemplate.attributes});
    await request('/lobbies/characters/'+player.id,{data:carrier.data},token,'PUT');carrier=await request('/lobbies/characters/'+player.id,null,playerToken,'GET');
    const bookIndex=carrier.data.inventory.pockets.findIndex(i=>i.id==='browser-one-book');
    await request('/lobbies/characters/'+player.id+'/portable-lights',{operation:'pda_import_book',path:['inventory','pockets',1],source_id:'radio-player',
        book_path:['inventory','pockets',bookIndex],character_revision:carrier.data._revision},playerToken);
    await request(api+'/weather',{sun:{enabled:true,intensity:1},rain:{enabled:false,intensity:0},fog:{enabled:false,intensity:0},emission:{enabled:false,intensity:0}},token,'PATCH');
    for(const client of [page,peer])await client.evaluate("document.querySelector('[data-pda-close]')?.click();window.closeCharacterSheet();true");
    await open(peer,actor.id,'dynamo-device');await open(page,player.id,'radio-player');
    const access=async client=>client.evaluate("[...document.querySelectorAll('#pda-shell-modal nav button')].find(b=>b.textContent==='Подключения').click();true");
    await access(peer);await access(page);
    await peer.until("document.querySelector('.pda-access')?.textContent.includes('Дешифратор 3-го порядка')",'Настоящий установленный дешифратор');
    await peer.until("[...document.querySelectorAll('.pda-access article')].some(e=>e.textContent.includes('radio-player'))",'Целевой сигнал доступен в ясную погоду');
    await peer.evaluate("(()=>{const card=[...document.querySelectorAll('.pda-access article')].find(e=>e.textContent.includes('radio-player'));[...card.querySelectorAll('button')].find(b=>b.textContent.includes('радиосвязь')).click()})()");
    await peer.until("document.querySelector('.pda-access')?.textContent.includes('осталось 30 мин')",'Взлом запущен через интерфейс');
    let clock=await request(api,null,token,'GET'),absolute=clock.game_day*1440+clock.game_time_minutes+30;
    await request(api+'/time',{game_day:Math.floor(absolute/1440),game_time_minutes:absolute%1440},token,'PATCH');
    await peer.until("[...document.querySelectorAll('.pda-access button')].some(b=>b.textContent==='Управлять КПК')",'Доступ получен автоматически по времени');
    await page.until("document.querySelector('.pda-access')?.textContent.includes('Контратака')",'Владелец получил предупреждение и средства защиты');
    await peer.evaluate("[...document.querySelectorAll('.pda-access button')].find(b=>b.textContent==='Управлять КПК').click();true");
    await peer.until("document.querySelector('[aria-label=\"Заметки подключённого КПК\"]')",'Открыт активный аккаунт целевого устройства');
    await peer.evaluate("document.querySelector('[aria-label=\"Заметки подключённого КПК\"]').value='Запись через взломанный КПК';[...document.querySelectorAll('.pda-access button')].find(b=>b.textContent==='Сохранить заметки').click();true");
    await peer.until("document.querySelector('[aria-label=\"Заметки подключённого КПК\"]')?.value==='Запись через взломанный КПК'&&[...document.querySelectorAll('.pda-access button')].some(b=>b.textContent==='Сохранить заметки'&&!b.disabled)", 'Заметки сохранены и подтверждены ответом сервера');
    const account=await request('/lobbies/characters/'+player.id+'/pda-account?item_path='+encodeURIComponent(JSON.stringify(['inventory','pockets',1]))+'&item_id=radio-player',null,playerToken,'GET');
    assert.equal(account.account.notes,'Запись через взломанный КПК');
    await peer.evaluate("[...document.querySelectorAll('.pda-access-tabs button')].find(b=>b.textContent==='Общий чат').click();true");
    await peer.until("document.querySelector('.pda-access [data-radio-send] textarea')",'Существующий интерфейс радиочата подключён к целевому КПК');
    await peer.evaluate("document.querySelector('.pda-access [data-radio-send] textarea').value='Передача из целевого устройства';document.querySelector('.pda-access [data-radio-send]').requestSubmit();true");
    await peer.until("document.querySelector('.pda-access [data-radio-messages]')?.textContent.includes('Передача из целевого устройства')",'Реальная передача из чужого аккаунта');
    const geometry=await peer.evaluate("(()=>{const r=document.querySelector('.pda-shell').getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth&&['[data-pda-close]','.pda-shell-footer'].every(selector=>{const b=document.querySelector(selector).getBoundingClientRect();return b.top>=r.top&&b.bottom<=r.bottom})})()");assert.ok(geometry,'Рамка, кнопка закрытия и нижние органы управления видимы без внешней прокрутки');
    const shot=await peer.call('Page.captureScreenshot',{format:'png'});
    require('node:fs').writeFileSync(require('node:path').join(safeSession.artifacts,'pda-access.png'),Buffer.from(shot.data,'base64'));
    await peer.until("[...document.querySelectorAll('.pda-access-tabs button')].some(b=>b.textContent==='Файлы'&&!b.disabled)",'Передача завершена, разделы доступны');
    await peer.evaluate("[...document.querySelectorAll('.pda-access-tabs button')].find(b=>b.textContent==='Файлы').click();true");
    await peer.until("[...document.querySelectorAll('.pda-access button')].some(b=>b.textContent==='Читать · 4 часа'&&!b.disabled)",'Книга доступна через живое соединение');
    await peer.evaluate("[...document.querySelectorAll('.pda-access button')].find(b=>b.textContent==='Читать · 4 часа').click();true");
    await peer.until("document.querySelector('.pda-access')?.textContent.includes('осталось 240 мин')",'Используется обычная работа чтения на четыре часа');
    const distant=await request(api+'/locations',{name:'Дальняя проверка связи',world_tile_x:12,world_tile_z:8,grid_width:60,grid_height:60},token);
    await request(api+'/locations/'+distant.id+'/spawn_character',{character_id:player.id,tile_x:43,tile_y:48},token);
    await peer.until("!document.querySelector('.pda-access [data-radio-send]')",'Старый экран потерял доступ после выхода из радиуса');
    await peer.until("(async()=>!(await(await import('/static/js/api.js')).Server.getCrafting("+actor.id+")).state.current)()",'Потеря доступа автоматически прервала незавершённое чтение');
    await request(loc+'/spawn_character',{character_id:player.id,tile_x:43,tile_y:48},token);
    await peer.until("document.querySelector('.pda-access')?.textContent.includes('Потеряна двусторонняя радиосвязь')",'Возврат не восстановил прежний взлом');
    await peer.until("[...document.querySelectorAll('.pda-access article')].some(e=>e.textContent.includes('radio-player')&&[...e.querySelectorAll('button')].some(b=>b.textContent.includes('Начать взлом')))",'Сигнал снова доступен только для нового взлома');
    await peer.evaluate("(()=>{const card=[...document.querySelectorAll('.pda-access article')].find(e=>e.textContent.includes('radio-player')&&e.querySelector('button'));[...card.querySelectorAll('button')].find(b=>b.textContent.includes('радиосвязь')).click()})()");
    await peer.until("document.querySelector('.pda-access')?.textContent.includes('осталось 30 мин')",'Повторный сеанс создан заново');
    clock=await request(api,null,token,'GET');absolute=clock.game_day*1440+clock.game_time_minutes+30;
    await request(api+'/time',{game_day:Math.floor(absolute/1440),game_time_minutes:absolute%1440},token,'PATCH');
    await peer.until("[...document.querySelectorAll('.pda-access button')].some(b=>b.textContent==='Управлять КПК')",'Новый доступ получен');
    await peer.evaluate("[...document.querySelectorAll('.pda-access button')].find(b=>b.textContent==='Управлять КПК').click();true");
    await peer.until("[...document.querySelectorAll('.pda-access button')].some(b=>b.textContent==='Читать · 4 часа'&&!b.disabled)",'Непрочитанная книга после отмены');
    await peer.evaluate("[...document.querySelectorAll('.pda-access button')].find(b=>b.textContent==='Читать · 4 часа').click();true");
    await peer.until("document.querySelector('.pda-access')?.textContent.includes('осталось 240 мин')",'Повторное чтение с начала');
    clock=await request(api,null,token,'GET');absolute=clock.game_day*1440+clock.game_time_minutes+240;
    await request(api+'/time',{game_day:Math.floor(absolute/1440),game_time_minutes:absolute%1440},token,'PATCH');
    await peer.until("[...document.querySelectorAll('.pda-access button')].some(b=>b.textContent==='Прочитано'&&b.disabled)",'Чтение завершилось автоматически');
    const learned=await request('/lobbies/characters/'+actor.id+'/crafting',null,token,'GET');assert.ok(learned.state.library.some(e=>e.skill_path==='physical.strength'));
    carrier=await request('/lobbies/characters/'+player.id,null,playerToken,'GET');
    assert.equal(carrier.data.inventory.pockets[1].contents.length,1,'Книга остаётся на исходном физическом КПК');
    assert.ok(!JSON.stringify(learned.inventory).includes(bookTemplate.name),'На КПК читателя копия не создаётся');
    await page.until("[...document.querySelectorAll('.pda-access button')].some(b=>b.textContent==='Контратака · 3 ОД')",'Контратака доступна владельцу нового подключения');
    await page.evaluate("[...document.querySelectorAll('.pda-access button')].find(b=>b.textContent==='Контратака · 3 ОД').click();true");
    await peer.until("document.querySelector('[data-pda-status]')?.textContent==='НЕИСПРАВЕН'",'Контратака физически сожгла атакующий КПК и обновила его экран');
    console.log('Взлом КПК: два клиента Припяти, действительная установка утилит, 30 минут игрового времени, чужие заметки и радиопередача, оповещение защиты и необратимый отзыв после потери связи; 1920×1080.');
};
