// Погода на копии Припяти: GPU, два клиента, серверное скрытие и физические крыши.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {loadFixture,waitForDocument}=require('./durable_reload.browser.cjs');
module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'weather');assert.ok(safeSession.verified);
    const api=`/lobbies/${lobby.id}`,{fixture}=loadFixture();
    const location=await request(api+'/locations',{...fixture,objects:undefined,world_tile_x:7,world_tile_z:8},token),loc=api+'/locations/'+location.id;
    await request(loc+'/objects/batch',{objects:fixture.objects},token);
    await request('/auth/register',{username:'weather-observer',email:'weather-observer@example.test',password:'synthetic-weather-password'});
    const observer=await request('/auth/login',{username:'weather-observer',password:'synthetic-weather-password'});
    await request(api+'/join',{},observer.access_token);
    let protocol,context,peer;
    try{
        const version=await(await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();protocol=await connect(version.webSocketDebuggerUrl);
        context=await protocol.call('Target.createBrowserContext');const t=await protocol.call('Target.createTarget',{url:base,browserContextId:context.browserContextId});
        const tabs=await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json();peer=await connect(tabs.find(i=>i.id===t.targetId).webSocketDebuggerUrl);
        await peer.call('Page.enable');await peer.call('Runtime.enable');await waitForDocument(peer,{href:base,label:'Погодный наблюдатель'});
        await peer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(observer.access_token)});localStorage.setItem('user_id',${JSON.stringify(String(observer.user_id))});true`);
        for(const client of [page,peer]){
            const href=base+api+'/page?location='+location.id,epoch=await client.evaluate('performance.timeOrigin');
            await client.call('Page.navigate',{url:href});await waitForDocument(client,{href,afterEpoch:epoch,label:'Припять: погода'});
            await client.until(`window.socket?.connected&&window.currentLocationId===${location.id}&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Сцена готова');
        }
        const weatherChecks=[];
        const click=async selector=>{
            const p=await page.evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)return null;const r=e.getBoundingClientRect();return r.width&&r.height?{x:r.x+r.width/2,y:r.y+r.height/2}:null})()`);
            assert.ok(p,'Видимая кнопка '+selector);
            await page.call('Input.dispatchMouseEvent',{type:'mousePressed',...p,button:'left',clickCount:1});
            await page.call('Input.dispatchMouseEvent',{type:'mouseReleased',...p,button:'left',clickCount:1});
        };
        assert.equal(await peer.evaluate(`Boolean(document.querySelector('#location-time-btn,#location-weather-btn'))`),false,'Игрок не получает управление погодой/временем');
        await click('#location-time-btn');
        await page.until(`!document.getElementById('location-time-panel').hidden&&document.querySelector('#location-time-panel #lobby-game-day')`,'Исходные поля мирового времени доступны с подлокации');
        assert.equal(await page.evaluate(`document.querySelectorAll('#lobby-game-day').length`),1);
        await click('#lobby-game-day');
        for(const e of [{type:'keyDown',key:'Control',code:'ControlLeft',modifiers:2,windowsVirtualKeyCode:17},
            {type:'keyDown',key:'a',code:'KeyA',modifiers:2,windowsVirtualKeyCode:65},
            {type:'keyUp',key:'a',code:'KeyA',modifiers:2,windowsVirtualKeyCode:65},
            {type:'keyUp',key:'Control',code:'ControlLeft',modifiers:0,windowsVirtualKeyCode:17}])await page.call('Input.dispatchKeyEvent',e);
        await page.call('Input.insertText',{text:'2'});
        await page.call('Input.dispatchKeyEvent',{type:'keyDown',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});
        await page.call('Input.dispatchKeyEvent',{type:'keyUp',key:'Tab',code:'Tab',windowsVirtualKeyCode:9});
        for(const client of [page,peer])await client.until(`window.lobbyGameDay===2`,'Изменение суток из подлокации синхронизировано');
        const timeShot=await page.call('Page.captureScreenshot',{format:'png'});
        fs.writeFileSync(path.join(safeSession.artifacts,'location-global-time.png'),Buffer.from(timeShot.data,'base64'));
        await request(api+'/time',{game_day:1,game_time_minutes:480},token,'PATCH');
        await request(api+'/weather',{sun:{enabled:false,intensity:.5},rain:{enabled:false,intensity:.5},fog:{enabled:false,intensity:.5},emission:{enabled:false,intensity:.5}},token,'PATCH');
        await click('#location-weather-btn');
        await page.until(`getComputedStyle(document.querySelector('#settings-panel')).display!=='none'&&getComputedStyle(document.querySelector('#weather-settings')).display!=='none'`,'Открыта исходная вкладка погоды над подлокацией');
        await click('#weather-rain');
        await click('#weather-settings .btn');
        for(const client of [page,peer])await client.until(`window.weatherSettings?.rain?.enabled&&window.getLocationWeatherDiagnostics?.()?.drops===1500`,'Дождь изменён кнопками ГМа в подлокации');
        const weatherShot=await page.call('Page.captureScreenshot',{format:'png'});
        fs.writeFileSync(path.join(safeSession.artifacts,'location-weather-settings.png'),Buffer.from(weatherShot.data,'base64'));
        await page.evaluate(`window.closeSettings();true`);
        const denied=await peer.evaluate(`Promise.all(['/weather','/time'].map(p=>fetch(${JSON.stringify(api)}+p,{method:'PATCH',headers:{'Content-Type':'application/json',Authorization:'Bearer '+localStorage.getItem('access_token')},body:'{}'}).then(r=>r.status)))`);
        assert.deepEqual(denied,[403,403]);
        for(const client of [page,peer])await client.evaluate(`window.__weatherEvents=[];socket.on('weather_updated',s=>window.__weatherEvents.push(s));true`);
        for(const [name,settings,drops,fog] of [
            ['clear',{sun:{enabled:true,intensity:1},rain:{enabled:false,intensity:.5},fog:{enabled:false,intensity:.5}},0,false],
            ['rain',{sun:{enabled:true,intensity:.5},rain:{enabled:true,intensity:.5},fog:{enabled:false,intensity:.5}},1500,false],
            ['storm-fog',{sun:{enabled:true,intensity:.5},rain:{enabled:true,intensity:.8},fog:{enabled:true,intensity:.6}},2040,true],
        ]){
            await request(api+'/weather',settings,token,'PATCH');
            for(const client of [page,peer]){
                await client.until(`window.getLocationWeatherDiagnostics?.()?.attached&&window.getLocationWeatherDiagnostics?.()?.drops===${drops}&&Boolean(window.getLocationWeatherDiagnostics?.()?.fogDensity)===${fog}`,'Погода синхронизирована на подлокации: '+name);
                assert.ok(await client.evaluate(`window.__weatherEvents.length>0`));
            }
            await page.evaluate(`locationControls.target.set(24,1,25);locationControls.object.position.set(44,40,55);locationControls.update();true`);
            await page.evaluate(`new Promise(resolve=>{let n=0;function frame(){if(++n===12)resolve(true);else requestAnimationFrame(frame)}requestAnimationFrame(frame)})`);
            const frameTiming=await page.evaluate(`new Promise(resolve=>{let last=performance.now(),times=[];function frame(now){times.push(now-last);last=now;if(times.length===60){times.sort((a,b)=>a-b);resolve({medianFrameMs:times[30],p95FrameMs:times[57]})}else requestAnimationFrame(frame)}requestAnimationFrame(frame)})`);
            weatherChecks.push({name,...await page.evaluate(`window.getLocationWeatherDiagnostics()`),...frameTiming});
            if(name==='rain'){
                const button=await page.evaluate(`(()=>{const b=document.querySelector('.weather-audio-unlock');if(!b||b.hidden)return null;const r=b.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
                if(button){
                    await page.call('Input.dispatchMouseEvent',{type:'mousePressed',...button,button:'left',clickCount:1});
                    await page.call('Input.dispatchMouseEvent',{type:'mouseReleased',...button,button:'left',clickCount:1});
                }
                await page.until(`document.querySelector('.weather-audio-unlock')?.hidden`,'Существующий звук дождя воспроизводится в подлокации');
            }
            if(drops){
                const wet=await page.evaluate(`window.getLocationWeatherDiagnostics()`);
                assert.ok(wet.puddles>0&&wet.puddles<=192,'Лужицы добавлены ограниченным общим мешем');
                assert.ok(wet.puddleSamples.every(p=>p.flatness<=.025&&p.height-p.ground<=.04),'Лужицы прилегают к ровной поверхности');
                const floors=fixture.objects.filter(o=>o.type==='floor'&&o.properties?.building_id&&Number(o.properties?.level||0)>0);
                assert.ok(floors.length>0);
                const probes=await page.evaluate(`(${JSON.stringify(floors.slice(0,12).map(o=>({x:o.tile_x+.5,z:o.tile_y+.5})))}).map(p=>window.getLocationWeatherDiagnostics(p))`);
                assert.ok(probes.some(p=>p.surfaceHeight>p.groundHeight+1),'Дождь останавливается на физическом перекрытии');
            }
            const shot=await page.call('Page.captureScreenshot',{format:'png'});
            fs.writeFileSync(path.join(safeSession.artifacts,'weather-'+name+'.png'),Buffer.from(shot.data,'base64'));
            if(name==='rain'){
                const p=await page.evaluate(`window.getLocationWeatherDiagnostics().puddleSamples[0]`);
                await page.evaluate(`locationControls.target.set(${p.x},${p.ground},${p.z});locationControls.object.position.set(${p.x+.3},${p.ground+5},${p.z+.3});locationControls.update();true`);
                await page.evaluate(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))))`);
                const close=await page.call('Page.captureScreenshot',{format:'png'});
                fs.writeFileSync(path.join(safeSession.artifacts,'rain-puddles-close.png'),Buffer.from(close.data,'base64'));
                await page.evaluate(`locationControls.object.position.set(${p.x-3},${p.ground+4},${p.z-3});locationControls.update();true`);
                await page.evaluate(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))))`);
                const angle=await page.call('Page.captureScreenshot',{format:'png'});
                fs.writeFileSync(path.join(safeSession.artifacts,'rain-puddles-angle.png'),Buffer.from(angle.data,'base64'));
            }
        }
        const fogZoom=[];
        for(const intensity of [.5,1]){
            await request(api+'/weather',{fog:{enabled:true,intensity},rain:{enabled:false,intensity:.5},sun:{enabled:false,intensity:.5}},token,'PATCH');
            await page.until(`window.getLocationWeatherDiagnostics?.()?.fogStrength===${intensity}`,'Задана интенсивность тумана');
            for(const [zoom,offset] of [['far',[20,39,30]],['near',[6,10,8]]]){
                await page.evaluate(`locationControls.target.set(24,1,25);locationControls.object.position.set(${24+offset[0]},${1+offset[1]},${25+offset[2]});locationControls.update();true`);
                await page.evaluate(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))))`);
                fogZoom.push(await page.evaluate(`({intensity:${intensity},zoom:${JSON.stringify(zoom)},density:getLocationWeatherDiagnostics().fogDensity,optical:getLocationWeatherDiagnostics().fogDensity*locationControls.object.position.distanceTo(locationControls.target),puddles:getLocationWeatherDiagnostics().puddles})`));
                const fogShot=await page.call('Page.captureScreenshot',{format:'png'});
                fs.writeFileSync(path.join(safeSession.artifacts,`fog-${intensity}-${zoom}.png`),Buffer.from(fogShot.data,'base64'));
            }
        }
        assert.ok(fogZoom.every(row=>row.puddles===0),'Лужицы исчезают после выключения дождя');
        assert.ok(Math.abs(fogZoom[0].optical-fogZoom[1].optical)<.03&&Math.abs(fogZoom[2].optical-fogZoom[3].optical)<.03,'При приближении туман не исчезает');
        assert.ok(fogZoom[2].optical>fogZoom[0].optical*1.5,'1,0 заметно плотнее 0,5');
        fs.writeFileSync(path.join(safeSession.artifacts,'fog-zoom.json'),JSON.stringify(fogZoom,null,2));
        await page.evaluate(`locationControls.object.position.set(8,22,45);locationControls.update();true`);
        await page.evaluate(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))))`);
        const weatherAngle=await page.call('Page.captureScreenshot',{format:'png'});
        fs.writeFileSync(path.join(safeSession.artifacts,'weather-second-angle.png'),Buffer.from(weatherAngle.data,'base64'));
        await request(api+'/weather',{sun:{enabled:false,intensity:1},rain:{enabled:false,intensity:.5},fog:{enabled:false,intensity:.5}},token,'PATCH');
        fs.writeFileSync(path.join(safeSession.artifacts,'weather-checks.json'),JSON.stringify(weatherChecks,null,2));
        const room=fixture.objects.find(o=>o.type==='floor'&&o.properties?.building_id&&Number(o.properties?.level||0)===0);
        assert.ok(room);
        const hidden=[];
        for(const [key,x,y] of [['batut',0,0],['vspishka',0,1],['vspishka',room.tile_x,room.tile_y]]){
            hidden.push(await request(loc+'/objects',{name:'Погодная проверка '+key,type:'anomaly',tile_x:x,tile_y:y,
                properties:{anomaly_key:key,gm_hidden:true}},token));
        }
        for(const client of [page,peer])await client.evaluate(`window.__revealed=[];socket.on('location_object_updated',e=>window.__revealed.push(e.object));true`);
        await request(api+'/weather',{rain:{enabled:true,intensity:.5}},token,'PATCH');
        for(const client of [page,peer])await client.until(`window.__revealed.some(o=>o.id===${hidden[0].id}&&o.properties.gm_hidden===false)&&window.__revealed.some(o=>o.id===${hidden[1].id}&&o.properties.gm_hidden===false)`,'Два клиента получили раскрытие гравитационной и электроаномалии');
        let saved=await request(loc,null,token,'GET');
        assert.equal(saved.objects.find(o=>o.id===hidden[2].id).properties.gm_hidden,true,'В помещении скрытие сохранено');
        const again=await request(api+'/locations/objects/'+hidden[0].id,{properties:{gm_hidden:true}},token,'PATCH');
        assert.equal(again.properties.gm_hidden,false,'H не скрывает раскрываемую дождём аномалию на улице');
        const before=saved.tiles_data[0][0].objects||[];
        const edited=await request(loc+'/object-edits',{tiles:[{x:0,z:0,before,after:[...before,{type:'anomaly',anomalyKey:'batut',gm_hidden:true}]}]},token);
        assert.equal(edited.updates[0].objects.at(-1).gm_hidden,false,'Редактор раскрывает и клеточный формат аномалии');
        await request(api+'/weather',{rain:{enabled:false,intensity:.5}},token,'PATCH');
        for(const obj of hidden.slice(0,2))await request(api+'/locations/objects/'+obj.id,{properties:{gm_hidden:true}},token,'PATCH');
        await request(api+'/weather',{rain:{enabled:true,intensity:.6}},token,'PATCH');
        saved=await request(loc,null,token,'GET');
        assert.equal(saved.objects.find(o=>o.id===hidden[0].id).properties.gm_hidden,true,'Ливень не раскрывает гравитационную');
        assert.equal(saved.objects.find(o=>o.id===hidden[1].id).properties.gm_hidden,false,'Ливень раскрывает электрическую');
        for(const client of [page,peer]){
            const epoch=await client.evaluate('performance.timeOrigin');
            await client.call('Page.reload',{ignoreCache:true});
            await waitForDocument(client,{href:base+api+'/page?location='+location.id,afterEpoch:epoch,label:'Погода после перезагрузки'});
            await client.until(`window.socket?.connected&&window.getLocationWeatherDiagnostics?.()?.attached&&window.getLocationWeatherDiagnostics?.()?.drops===1680`,'Погода сохранена после перезагрузки');
        }
        await request(api+'/weather',{sun:{enabled:false,intensity:.5},rain:{enabled:false,intensity:.5},fog:{enabled:false,intensity:.5}},token,'PATCH');
        for(const client of [page,peer])await client.until(`['sun','rain','fog'].every(k=>window.weatherSettings?.[k]?.enabled===false)`,'Исходная погода перед часовым переходом');
        await request(api+'/time',{game_day:1,game_time_minutes:540},token,'PATCH');
        for(const client of [page,peer])await client.until(`['sun','rain','fog'].filter(k=>window.weatherSettings?.[k]?.enabled).length===1&&['sun','rain','fog'].some(k=>window.weatherSettings?.[k]?.enabled&&[.4,.6].includes(window.weatherSettings[k].intensity))`,'Часовая смена погоды синхронизирована на обоих клиентах');
        fs.writeFileSync(path.join(safeSession.artifacts,'weather-gameplay.json'),JSON.stringify({twoClients:true,indoorHidden:true,rainGravityAndElectric:true,stormOnlyElectric:true,legacyTiles:true},null,2));
        await click('#exit-location-btn');
        await page.until(`!window.isLocationActive&&document.querySelector('.lobby-menu #lobby-game-day')&&getComputedStyle(document.querySelector('.lobby-menu')).display!=='none'`,'Поля времени возвращены на глобальную карту');
        assert.equal(await page.evaluate(`document.querySelectorAll('#lobby-game-day').length`),1);
        assert.equal(await page.evaluate(`Boolean(document.querySelector('#location-time-btn,#location-weather-btn'))`),false);
    }finally{await peer?.close();if(context)await protocol?.call('Target.disposeBrowserContext',{browserContextId:context.browserContextId});await protocol?.close();}
};
