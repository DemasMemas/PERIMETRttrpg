// Проверка реального управления счётчиками и сохранения после смены вкладки.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,characterId,safeSession})=>{
    await page.evaluate(`window.openCharacterSheet(${characterId},'basic').then(()=>true)`);
    await page.until("document.querySelector('.health-vitals-grid > .health-needs-panel')",'Еда и вода рядом со статусами');
    const buttonSelector=(key,delta)=>`[onclick="adjustDailyNeed('${key}',${delta})"]`;
    for(const key of ['mealsToday','drinksToday']){
        const count=await page.evaluate(`document.querySelectorAll('[name="health.needs.${key}"]').length`);assert.equal(count,1);
        assert.equal(await page.evaluate(`document.querySelector(${JSON.stringify(buttonSelector(key,-1))}).disabled`),true);
    }
    const click=async(key,delta)=>{
        const selector=buttonSelector(key,delta);
        await page.evaluate(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`);
        const point=await page.evaluate(`(()=>{const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
        for(const type of ['mouseMoved','mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{type,...point,button:type==='mouseMoved'?'none':'left',buttons:type==='mousePressed'?1:0,clickCount:type==='mouseMoved'?0:1});
    };
    await click('mealsToday',1);await click('mealsToday',1);await click('mealsToday',1);
    assert.equal(await page.evaluate(`document.querySelector(${JSON.stringify(buttonSelector('mealsToday',1))}).disabled`),true);
    await click('mealsToday',-1);await click('drinksToday',1);
    await page.until(`(async()=>{const {Server}=await import('/static/js/api.js');const r=await Server.getCharacter(${characterId});return r.data.health.needs?.mealsToday===2&&r.data.health.needs?.drinksToday===1})()`,'Еда и вода сохранены на сервере');
    for(const [width,height] of [[1920,1080],[1366,768]]){
        await page.call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
        await page.evaluate("document.querySelector('.health-vitals-grid').scrollIntoView({block:'center'});new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))");
        const layout=await page.evaluate(`(()=>{const panel=document.querySelector('.health-needs-panel'),vitals=panel.parentElement.getBoundingClientRect();return [...panel.querySelectorAll('.health-need-controls')].map(e=>{const r=e.getBoundingClientRect();return{height:r.height,inside:r.x>=vitals.x&&r.right<=vitals.right,visible:r.top>=0&&r.bottom<=innerHeight}})})()`);
        assert.ok(layout.every(r=>r.height===38&&r.inside&&r.visible));
        fs.writeFileSync(path.join(safeSession.artifacts,`daily-needs-${width}.png`),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    }
    await page.evaluate("document.querySelector('#sheet-tabs [data-tab=equipment]').click();document.querySelector('#sheet-tabs [data-tab=basic]').click();true");
    assert.equal(await page.evaluate("document.querySelector('[name=\"health.needs.mealsToday\"]').value"),'2');
    console.log('Еда/вода: компактные счётчики, реальные +/−, границы0/3, сохранение; размещение1920×1080 и1366×768 проверено.');
};
