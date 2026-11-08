// Проверка расположения локального экрана на реальных данных текущего прогона.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'equipment');assert.ok(safeSession.verified);
    const {createReview,safeFile}=require('./helpers/playtest_review.cjs'),review=createReview(),state=review.scan();
    assert.equal(state.cases.filter(c=>c.run?.runId===path.basename(safeSession.artifacts)).length,2);
    await page.evaluate('window.closeCharacterSheet();true');
    const html=fs.readFileSync(path.join(__dirname,'helpers/playtest_review.html'),'utf8')
        .replace(/<link[^>]+>/g,'').replace(/<script[^>]+><\/script>/g,'');
    const css=fs.readFileSync(path.join(__dirname,'helpers/playtest_review.css'),'utf8');
    const script=fs.readFileSync(path.join(__dirname,'helpers/playtest_review.js'),'utf8');
    const imageMap={};for(const item of state.cases)for(const name of item.run?.images||[]){
        imageMap[`/image/${item.run.runId}/${name}`]='data:image/png;base64,'+fs.readFileSync(safeFile(path.dirname(safeSession.artifacts),item.run.runId,name)).toString('base64');
    }
    await page.evaluate(`(()=>{document.documentElement.innerHTML=${JSON.stringify(html.replace(/^.*?<html[^>]*>|<\/html>\s*$/gs,''))};
        const style=document.createElement('style');style.textContent=${JSON.stringify(css)};document.head.append(style);
        window.reviewLayoutCalls=[];window.fetch=async(url,options)=>{reviewLayoutCalls.push({url,method:options?.method||'GET'});
            if(url!='/api/state'||options?.method==='POST')throw Error('Проверка расположения не принимает результат за автора');
            return {ok:true,json:async()=>(${JSON.stringify({...state,token:'layout-only'})})};};
        const images=${JSON.stringify(imageMap)},observer=new MutationObserver(()=>{document.querySelectorAll('img').forEach(img=>{const value=images[img.getAttribute('src')];if(value)img.src=value;});});
        observer.observe(document.documentElement,{childList:true,subtree:true});
        (0,eval)(${JSON.stringify(script)});return true})()`);
    await page.until("document.querySelectorAll('#cases button').length===6",'Экран плейтеста построен');
    await page.evaluate("[...document.querySelectorAll('#cases button')].find(b=>b.textContent.includes('Пояс: четыре инструмента')).click();true");
    await page.until("document.getElementById('title')?.textContent==='Пояс: четыре инструмента'",'Случай с инструментами');
    assert.equal(await page.evaluate("document.getElementById('accept').disabled"),true,'Текущий стенд ещё не закрыт, приёмка запрещена');
    await page.evaluate("[...document.querySelectorAll('#content button')].find(b=>b.textContent==='Далее').click();true");
    await page.until("document.querySelector('.steps-heading')?.textContent.includes('Шаг 2 из')",'Пошаговый просмотр');
    for(const[width,height]of [[1920,1080],[1600,900],[1366,768],[1280,720]]){
        await page.call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
        await page.until("['repeat','comment','accept','rework'].every(id=>{const r=document.getElementById(id).getBoundingClientRect();return r.x>=0&&r.right<=innerWidth&&r.y>=0&&r.bottom<=innerHeight})",'Приёмка доступна без прокрутки '+width);
        assert.ok(await page.evaluate('document.documentElement.scrollWidth<=innerWidth'),'Нет горизонтального переполнения');
        if(width===1280)fs.writeFileSync(path.join(safeSession.artifacts,'playtest-review-1280x720.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    }
    assert.equal(await page.evaluate("reviewLayoutCalls.some(call=>call.method==='POST')"),false);
    console.log('Экран плейтеста: реальные записи текущего прогона, пошаговый просмотр, четыре настольных размера, приёмка текущего незакрытого стенда запрещена. HTTP-сервер и сохранение решений проверяются отдельно.');
};
