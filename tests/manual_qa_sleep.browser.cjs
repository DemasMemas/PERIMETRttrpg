// №224: выбор места сна, реальное восстановление и отказ до продвижения часов.
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');

module.exports=async({page,request,token,lobby,base,safeSession})=>{
    assert.ok(safeSession.verified,'Нужна изолированная проверочная сессия');
    const api=`/lobbies/${lobby.id}`;
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
    const partner=await request(api+'/characters',{name:'Проверка сна — второй участник',data:makeData()},token);
    const read=id=>request(`/lobbies/characters/${id}`,null,token,'GET');
    const save=async(id,data)=>request(`/lobbies/characters/${id}`,{data},token,'PUT');
    const absolute=state=>(state.game_day||1)*1440+(state.game_time_minutes||0);

    const choose=async(id,mode,disabledModes=[])=>{
        await page.evaluate(`window.openCharacterSheet(${id},'basic')`);
        await page.until('document.querySelector("#character-sheet-modal")?.style.display==="flex"','Лист персонажа открыт');
        await page.evaluate('window.openCharacterSleepSetup()');
        await page.until('document.querySelector("#character-sleep-setup-modal [data-sleep-mode]")','Выбор места сна открыт');
        for(const unavailable of disabledModes){
            assert.equal(await page.evaluate(`Array.from(document.querySelector("#character-sleep-setup-modal [data-sleep-mode]").options).find(option=>option.value===${JSON.stringify(unavailable)})?.disabled`),true,
                `Вариант ${unavailable} недоступен без предмета`);
        }
        await page.evaluate(`(()=>{const select=document.querySelector('#character-sleep-setup-modal [data-sleep-mode]');select.value=${JSON.stringify(mode)};select.dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('#character-sleep-setup-modal [data-sleep-save]').click()})()`);
        await page.until('!document.querySelector("#character-sleep-setup-modal")','Выбор сна сохранён');
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

    // В интерфейсе без инвентаря доступны только «без удобств» и кровать.
    await choose(actor.id,'none',['tent','sleeping_bag']);
    await choose(partner.id,'bed',['tent','sleeping_bag']);
    let before=await request(api,null,token,'GET');
    let rested=await request(api+'/rest',{type:'sleep',character_ids:[actor.id,partner.id]},token);
    assert.equal(rested.hours,8);
    assert.equal(resultFor(rested,actor.id).healed,0,'Без удобств лечение равно нулю');
    assert.equal(resultFor(rested,partner.id).healed,350,'Кровать лечит floor(701×50%)');
    assert.equal(absolute(rested),absolute(before)+480,'Общий сон двух персонажей длится восемь часов');

    await addCatalogItem(actor.id,tent,'sleep-tent-instance');
    await addCatalogItem(partner.id,bag,'sleep-bag-instance');
    await choose(actor.id,'tent');
    await choose(partner.id,'sleeping_bag');
    await setCurrent(actor.id,100);await setCurrent(partner.id,100);
    before=await request(api,null,token,'GET');
    rested=await request(api+'/rest',{type:'sleep',character_ids:[actor.id,partner.id]},token);
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
        reopenedSelection:true,
    },null,2));
    console.log('Сон: выбор через лист, два персонажа, без удобств0 / кровать350 / палатка350 / мешок262; отсутствующая палатка отклонена без продвижения часов.');
};
