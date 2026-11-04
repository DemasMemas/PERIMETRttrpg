// Измерение переключения погоды после загрузки настоящей глобальной карты.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,base,safeSession})=>{
    await page.evaluate(`(async()=>{window.__weatherWorld=await import('/static/js/lobby3d.js');__weatherWorld.controls.target.set(128,0,128);__weatherWorld.camera.position.set(128,160,180);__weatherWorld.controls.update();return true})()`);
    const results=[];
    for(const [name,rain,fog] of [['clear',false,false],['rain',true,false],['fog',false,true],['rain-fog',true,true],['off',false,false]]){
        await page.evaluate(`window.__weatherSample={name:${JSON.stringify(name)},startedAt:performance.now(),glBefore:structuredClone(window.worldGlTimings)};
            socket.once('weather_updated',()=>requestAnimationFrame(()=>requestAnimationFrame(()=>{__weatherSample.readyAt=performance.now();__weatherSample.programs=__weatherWorld.renderer.info.programs.length;__weatherSample.glAfter=structuredClone(window.worldGlTimings)})));true`);
        await request('/lobbies/'+lobby.id+'/weather',{sun:{enabled:false,intensity:.5},rain:{enabled:rain,intensity:.5},fog:{enabled:fog,intensity:.5},emission:{enabled:false,intensity:.5}},token,'PATCH');
        await page.until(`Boolean(window.__weatherSample?.readyAt)`,'Кадр после переключения '+name);
        const sample=await page.evaluate(`JSON.parse(JSON.stringify(window.__weatherSample))`);
        sample.elapsedMs=sample.readyAt-sample.startedAt;results.push(sample);
        fs.writeFileSync(path.join(safeSession.artifacts,'world-weather.json'),JSON.stringify(results,null,2));
        console.log('Переключение погоды '+name+': '+Math.round(sample.elapsedMs)+' мс; программ '+sample.programs);
        assert.ok(Number.isFinite(sample.elapsedMs));
        if(name==='fog')assert.equal(sample.programs,results[1].programs,'Включение тумана не создаёт новые программы материалов карты');
        if(name==='rain-fog'){
            const shot=await page.call('Page.captureScreenshot',{format:'png'});
            fs.writeFileSync(path.join(safeSession.artifacts,'world-weather.png'),Buffer.from(shot.data,'base64'));
        }
    }
};
