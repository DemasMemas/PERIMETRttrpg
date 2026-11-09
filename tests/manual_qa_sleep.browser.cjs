// №224: выбор места сна, реальное восстановление и отказ до продвижения часов.
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {waitForDocument}=require('./durable_reload.browser.cjs');

module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession})=>{
    assert.ok(safeSession.verified,'Нужна изолированная проверочная сессия');
    const api=`/lobbies/${lobby.id}`;
    const username='sleep-player';
    const password='synthetic-sleep-password';
    await request('/auth/register',{username,password,email:'sleep-player@example.test'});
    const player=await request('/auth/login',{username,password});
    await request(api+'/join',{},player.access_token);
    const version=await (await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();
    let protocol,context,peer;
    try{
        protocol=await connect(version.webSocketDebuggerUrl);
        context=await protocol.call('Target.createBrowserContext');
        const tab=await protocol.call('Target.createTarget',{url:base,browserContextId:context.browserContextId});
        const tabs=await (await fetch(`http://127.0.0.1:${debugPort}/json`)).json();
        peer=await connect(tabs.find(entry=>entry.id===tab.targetId).webSocketDebuggerUrl);
        await peer.call('Page.enable');
        await waitForDocument(peer,{href:base,label:'Отдельный игрок для проверки сна'});
        await peer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(player.access_token)});
            localStorage.setItem('user_id',${JSON.stringify(String(player.user_id))});true`);
        const href=base+api+'/page',epoch=await peer.evaluate('performance.timeOrigin');
        await peer.call('Page.navigate',{url:href});
        await waitForDocument(peer,{href,afterEpoch:epoch,label:'Лобби второго участника'});
        await peer.until('window.socket?.connected&&window.isGM===false&&typeof window.openCharacterSheet==="function"',
            'Обычный игрок подключился');
        await verifySleep({page,peer,request,token,playerToken:player.access_token,api,safeSession});
    }finally{
        await peer?.close();
        if(context)await protocol?.call('Target.disposeBrowserContext',{browserContextId:context.browserContextId});
        await protocol?.close();
    }
};

async function verifySleep({page,peer,request,token,playerToken,api,safeSession}){
    const catalog=await request(api+'/templates?category=misc',null,token,'GET');
    const tent=catalog.global.find(item=>item.name==='Палатка'&&item.category==='misc'
        &&item.attributes?.import_source==='equipment_workbook');
    const bag=catalog.global.find(item=>item.name==='Спальный мешок'&&item.category==='misc'
        &&item.attributes?.import_source==='equipment_workbook');
    assert.ok(tent&&bag,'Предметы сна должны браться из действующего каталога');

    const makeData=()=>({health:{current:100,max:701,effects:[],exhaustion:0,infection:0,
        needs:{day:1,mealsToday:3,drinksToday:3,sleptToday:false,missedDays:{food:0,water:0,sleep:0}}},
        inventory:{pockets:[],backpack:[]},equipment:{},weapons:[]});
    const actor=await request(api+'/characters',{name:'Проверка сна — условия',data:makeData()},token);
    const partner=await request(api+'/characters',{name:'Проверка сна — второй участник',data:makeData()},playerToken);
    const read=id=>request(`/lobbies/characters/${id}`,null,token,'GET');
    const save=async(id,data)=>request(`/lobbies/characters/${id}`,{data},token,'PUT');
    const absolute=state=>(state.game_day||1)*1440+(state.game_time_minutes||0);

    const choose=async(id,mode,disabledModes=[])=>{
        const client=id===partner.id?peer:page;
        await client.evaluate(`window.openCharacterSheet(${id},'basic')`);
        await client.until('document.querySelector("#character-sheet-modal")?.style.display==="flex"','Лист персонажа открыт');
        await client.evaluate('window.openCharacterSleepSetup()');
        await client.until('document.querySelector("#character-sleep-setup-modal [data-sleep-mode]")','Выбор места сна открыт');
        for(const unavailable of disabledModes){
            assert.equal(await client.evaluate(`Array.from(document.querySelector("#character-sleep-setup-modal [data-sleep-mode]").options).find(option=>option.value===${JSON.stringify(unavailable)})?.disabled`),true,
                `Вариант ${unavailable} недоступен без предмета`);
        }
        await client.evaluate(`(()=>{const select=document.querySelector('#character-sleep-setup-modal [data-sleep-mode]');select.value=${JSON.stringify(mode)};select.dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('#character-sleep-setup-modal [data-sleep-save]').click()})()`);
        await client.until('!document.querySelector("#character-sleep-setup-modal")','Выбор сна сохранён');
        assert.equal((await read(id)).data.health.combatMeta.sleepMode,mode,'Режим сохранён через UI листа');
    };
    const addCatalogItem=async(id,item,instanceId)=>{
        const current=await read(id),data=structuredClone(current.data);
        data.inventory.backpack.push({id:instanceId,templateId:item.id,name:item.name,category:item.category,
            quantity:1,weight:item.weight,volume:item.volume,attributes:item.attributes});
        await save(id,data);
    };
    const setCurrent=async(id,value)=>{
        const current=await read(id),data=structuredClone(current.data);data.health.current=value;await save(id,data);
    };
    const resultFor=(response,id)=>response.characters.find(entry=>entry.character_id===id);
    const startFromGm=async(type)=>{
        await page.evaluate('window.closeCharacterSheet?.();window.openLobbyRestModal()');
        const partnerRow=`document.querySelector('input[name=lobby-rest-character][value="${partner.id}"]')`;
        await page.until(`${partnerRow}?.closest('label').querySelector('small')?.textContent.includes('кровать')`,
            'ГМ видит кровать, выбранную другим игроком');
        await page.evaluate(`(()=>{
            const ids=${JSON.stringify([actor.id,partner.id])};
            for(const input of document.querySelectorAll('input[name=lobby-rest-character]'))input.checked=ids.includes(Number(input.value));
            [...document.querySelectorAll('#lobby-rest-modal button')].find(
                button=>button.textContent===${JSON.stringify(type==='sleep'?'Сон 8 часов':'Отдых 1 час')}).click();
        })()`);
        await page.until("document.querySelector('#lobby-rest-modal').style.display==='none'", 'ГМ завершил отдых');
    };

    // В интерфейсе без инвентаря доступны только «без удобств» и кровать.
    await choose(actor.id,'none',['tent','sleeping_bag']);
    await choose(partner.id,'bed',['tent','sleeping_bag']);
    const restParticipants=await Promise.all([read(actor.id),read(partner.id)]);
    let before=await request(api,null,token,'GET');
    await startFromGm('rest');
    assert.equal(absolute(await request(api,null,token,'GET')),absolute(before)+60,'Обычный отдых длится час');
    for(const participant of restParticipants){
        const {current,max}=participant.data.health;
        assert.equal((await read(participant.id)).data.health.current,Math.min(max,current+Math.floor(max*.05)));
    }
    await setCurrent(actor.id,100);await setCurrent(partner.id,100);
    const [beforeNone,beforeBed]=await Promise.all([read(actor.id),read(partner.id)]);
    before=await request(api,null,token,'GET');
    await startFromGm('sleep');
    assert.equal((await read(actor.id)).data.health.current,beforeNone.data.health.current,'Без удобств лечение равно нулю');
    const bedHealth=beforeBed.data.health;
    assert.equal((await read(partner.id)).data.health.current,
        Math.min(bedHealth.max,bedHealth.current+Math.floor(bedHealth.max*.5)),'Кровать восстанавливает 50% максимума');
    assert.equal(absolute(await request(api,null,token,'GET')),absolute(before)+480,'Общий сон двух персонажей длится восемь часов');

    await addCatalogItem(actor.id,tent,'sleep-tent-instance');
    await addCatalogItem(partner.id,bag,'sleep-bag-instance');
    await choose(actor.id,'tent');
    await choose(partner.id,'sleeping_bag');
    await setCurrent(actor.id,100);await setCurrent(partner.id,100);
    before=await request(api,null,token,'GET');
    const rested=await request(api+'/rest',{type:'sleep',character_ids:[actor.id,partner.id]},token);
    assert.equal(resultFor(rested,actor.id).healed,350,'Палатка лечит floor(701×50%)');
    assert.equal(resultFor(rested,partner.id).healed,262,'Мешок лечит floor(701×37,5%)');
    assert.equal(absolute(rested),absolute(before)+480);

    // Повторное открытие показывает сохранённый выбор; пропавший предмет блокирует сон до сдвига времени.
    await choose(actor.id,'tent');
    let actorState=await read(actor.id),actorData=structuredClone(actorState.data);
    actorData.inventory.backpack=actorData.inventory.backpack.filter(item=>item.id!=='sleep-tent-instance');
    await save(actor.id,actorData);
    await choose(actor.id,'none',['tent']);
    actorState=await read(actor.id);actorData=structuredClone(actorState.data);
    actorData.health.combatMeta.sleepMode='tent';await save(actor.id,actorData);
    before=await request(api,null,token,'GET');
    let rejected=false;
    try{await request(api+'/rest',{type:'sleep',character_ids:[actor.id]},token);}catch(error){rejected=/\b(400|422)\b/.test(error.message);}
    assert.ok(rejected,'Сон с выбранной, но отсутствующей палаткой отклоняется сервером');
    const after=await request(api,null,token,'GET');
    assert.equal(absolute(after),absolute(before),'Отклонённый сон не продвигает игровые часы');
    fs.writeFileSync(path.join(safeSession.artifacts,'manual-qa-sleep.json'),JSON.stringify({
        realSheetSelection:true,characters:2,none:0,bed:350,tent:350,sleepingBag:262,
        eightHoursPerSleep:true,missingTentRejected:true,clockUnchangedOnRejection:true,
        reopenedSelection:true,independentPlayer:true,gmSeesOtherPlayersMode:true,ordinaryRestMinutes:60,
    },null,2));
    console.log('Сон: два клиента, ГМ видит режим другого игрока, час отдыха и восемь часов сна. Проверены четыре режима и отказ при пропавшей палатке.');
}
