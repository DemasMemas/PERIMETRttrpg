// Настоящий сахар из каталога и три вида воды через обычную кнопку использования.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,offered,loc,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'effects');assert.ok(safeSession.verified);
    await page.call('Emulation.setDeviceMetricsOverride',{width:1920,height:1080,deviceScaleFactor:1,mobile:false});
    const make=(template,id)=>({id,templateId:template.id,name:template.name,category:template.category,
        attributes:structuredClone(template.attributes||{}),quantity:1,uses:3,maxUses:3,weight:template.weight,volume:template.volume});
    for(const[caseId,waterName,radiation,exhaustion]of [
        ['water-swamp','Болотная вода',20,1],['water-rain','Дождевая вода',10,0],['water-radioactive','Радиоактивная вода',6,0]]){
        const evidence=require('./helpers/playtest_registry.cjs').evidenceWriter(safeSession,caseId);
        const water=make(offered.global.find(t=>t.name===waterName),caseId+'-bottle'),sugar=make(offered.global.find(t=>t.name==='Сахар'),caseId+'-sugar');
        const owner=await request(`/lobbies/${lobby.id}/characters`,{name:'Сахар и '+waterName.toLocaleLowerCase('ru'),
            data:{health:{radiation:0,exhaustion:0,stress:2},inventory:{pockets:[water,sugar],backpack:[]}}},token);
        await request(loc+'/spawn_character',{character_id:owner.id,tile_x:47,tile_y:49},token);
        await page.evaluate(`window.openCharacterSheet(${owner.id},'inventory').then(()=>true)`);
        const button='[data-path="inventory,pockets,1"] button[title="Использовать"]';
        await page.until(`document.querySelector(${JSON.stringify(button)})`,'Сахар в настоящем инвентаре');
        await page.evaluate(`document.querySelector(${JSON.stringify(button)}).click();true`);
        await page.until("document.querySelector('[data-path=\"inventory,pockets,1\"]')?.textContent.includes('2/3')",'Сахар использован');
        const after=await request('/lobbies/characters/'+owner.id,null,token,'GET');
        assert.equal(after.data.health.radiation,radiation);assert.equal(after.data.health.exhaustion,exhaustion);assert.equal(after.data.health.stress,1);
        assert.equal(after.data.inventory.pockets[0].uses,1);assert.equal(after.data.inventory.pockets[1].uses,2);
        evidence.step('Сахар использован с двумя порциями воды',
            {water:waterName,waterUses:3,sugarUses:3,radiation:0,exhaustion:0,stress:2},
            {water:waterName,waterUses:after.data.inventory.pockets[0].uses,sugarUses:after.data.inventory.pockets[1].uses,
                radiation:after.data.health.radiation,exhaustion:after.data.health.exhaustion,stress:after.data.health.stress});
        await page.evaluate(`window.openCharacterSheet(${owner.id},'basic').then(()=>true)`);
        await page.until(`Number(document.querySelector('[name="health.radiation"]')?.value)===${radiation}`,'Радиация в игровом листе');
        await page.evaluate("(()=>{const input=document.querySelector('[name=\"health.radiation\"]');let parent=input.parentElement;while(parent){if(parent.tagName==='DETAILS')parent.open=true;parent=parent.parentElement;}input.scrollIntoView({block:'center'});return true})()");
        await page.until("(()=>{const r=document.querySelector('[name=\"health.radiation\"]').getBoundingClientRect();return r.width>0&&r.y>=0&&r.bottom<=innerHeight})()",'Показания действительно видны');
        const shot=caseId+'.png';fs.writeFileSync(path.join(safeSession.artifacts,shot),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        evidence.publish({images:[shot],controls:{random:'Бросков нет',time:'Игровое время не продвигается'},
            limitations:['Проверяется сахар с двумя порциями воды. Другие продукты и альтернативные компоненты — отдельные случаи.',
                'Клиент ГМа. Права отдельного игрока этим случаем не подтверждаются.']});
    }
    console.log('Вода-компонент: болотная, дождевая и радиоактивная вода с каталоговым сахаром; порядок эффектов, радиация/истощение/стресс, расходы и настоящий лист.');
};
