// Три настоящих фонаря в ночной Припяти: один ракурс, выключено/включено.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,offered,loc,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'lights');assert.ok(safeSession.verified);
    await page.call('Emulation.setDeviceMetricsOverride',{width:1920,height:1080,deviceScaleFactor:1,mobile:false});
    const templates=['Налобный фонарик','Фонарик','Улучшенный фонарик'].map(name=>{
        const item=offered.global.find(t=>t.name===name&&t.category!=='weapon_module');assert.ok(item,name);return item;});
    const items=templates.map((t,index)=>({id:'nearfield-'+index,templateId:t.id,name:t.name,category:t.category,
        quantity:1,weight:t.weight,volume:t.volume,attributes:structuredClone(t.attributes||{})}));
    const battery=offered.global.find(t=>t.name==='Батарейки');assert.ok(battery);
    const owner=await request(`/lobbies/${lobby.id}/characters`,{name:'Проверка ближнего света',data:{
        weapons:[{id:'light-test-rifle',name:'Проверочная винтовка',category:'weapon',weight:1,volume:6,damage:10,range:20}],
        inventory:{pockets:[...items,{id:'light-test-battery',templateId:battery.id,name:battery.name,category:battery.category,
            quantity:1,weight:battery.weight,volume:battery.volume,attributes:structuredClone(battery.attributes||{})}],backpack:[]}}},token);
    await request(loc+'/spawn_character',{character_id:owner.id,tile_x:2,tile_y:30},token);
    await request(loc+'/characters/'+owner.id+'/facing',{facing_x:0,facing_y:1},token,'PATCH');
    const detail=await request(loc,null,token,'GET');
    await request(loc,{lighting:{...detail.lighting,mode:'manual',ambient_intensity:.03,directional_intensity:0,background_color:"#0b1018"}},token,'PUT');
    await page.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');window.T=await import('three');window.FG=L.sceneGraphicsForTest();
        locationControls.enableDamping=false;locationControls.target.set(2.5,1,39);locationControls.object.position.set(-7,12,31);locationControls.update();return true})()`);
    const sample=()=>page.evaluate(`(()=>{const {renderer,scene,camera}=FG;renderer.render(scene,camera);const gl=renderer.getContext(),out=[];
        const ground=FG.characters.get(${owner.id}).model.position.y;
        for(const [distance,lateral] of [[1.5,0],[3,0],[6,0],[12,0],[15,0],[18,0],[19,0],[19.7,0],[20.2,0],[20.2,2],[6,2],[15,3],[22,0]]){const point=new T.Vector3(2.5+lateral,ground+.025,30.5+distance).project(camera);
            const x=Math.round((point.x+1)*.5*gl.drawingBufferWidth),y=Math.round((point.y+1)*.5*gl.drawingBufferHeight),pixels=new Uint8Array(5*5*4);
            if(x<3||y<3||x>=gl.drawingBufferWidth-3||y>=gl.drawingBufferHeight-3)throw Error('Проба вне кадра');
            gl.readPixels(x-2,y-2,5,5,gl.RGBA,gl.UNSIGNED_BYTE,pixels);let brightness=0;for(let i=0;i<pixels.length;i+=4)brightness+=(pixels[i]+pixels[i+1]+pixels[i+2])/3;
            out.push({distance,lateral,brightness:brightness/25});}return out})()`);
    const shot=async name=>fs.writeFileSync(path.join(safeSession.artifacts,name+'.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    const performanceRows=[];
    const measure=async (state,overviewOnly=false)=>{
        const results=await page.evaluate(`(async()=>{
            const {renderer,camera,controls}=FG,stats=a=>{a.sort((x,y)=>x-y);return{median:a[Math.floor(a.length*.5)],p95:a[Math.floor(a.length*.95)]}};
            const gl=renderer.getContext(),ext=renderer.capabilities.isWebGL2&&gl.getExtension('EXT_disjoint_timer_query_webgl2');
            const out=[];for(const view of [{name:'near',position:[-7,12,31],target:[2.5,1,39]},{name:'tactical',position:[26,32,67],target:[18,1,37]},{name:'overview',position:[86,49,86],target:[36,1,36]},{name:'away',position:[-7,12,31],target:[-25,1,16]}]){
                if(${overviewOnly}&&view.name!=='overview')continue;
                controls.target.set(...view.target);camera.position.set(...view.position);controls.update();
                for(let i=0;i<25;i++)await new Promise(requestAnimationFrame);
                const cpu=[],frames=[],gpu=[],queries=[],original=renderer.render;let last;
                renderer.render=function(...args){const at=performance.now(),q=ext&&gl.createQuery();if(q)gl.beginQuery(ext.TIME_ELAPSED_EXT,q);
                    try{return original.apply(this,args)}finally{if(q){gl.endQuery(ext.TIME_ELAPSED_EXT);queries.push(q)}cpu.push(performance.now()-at)}};
                try{for(let i=0;i<100;i++)await new Promise(resolve=>requestAnimationFrame(t=>{if(last)frames.push(t-last);last=t;resolve()}));}
                finally{renderer.render=original;}
                if(ext){for(let i=0;i<10;i++)await new Promise(requestAnimationFrame);for(const q of queries){
                    if(gl.getQueryParameter(q,gl.QUERY_RESULT_AVAILABLE)&&!gl.getParameter(ext.GPU_DISJOINT_EXT))gpu.push(gl.getQueryParameter(q,gl.QUERY_RESULT)/1e6);gl.deleteQuery(q)}}
                out.push({view:view.name,frameMs:stats(frames),cpuMs:stats(cpu),gpuMs:gpu.length?stats(gpu):null,calls:renderer.info.render.calls,triangles:renderer.info.render.triangles});
            }controls.target.set(2.5,1,39);camera.position.set(-7,12,31);controls.update();return out;
        })()`);performanceRows.push({state,results});
    };
    const rows=[];
    let reservedSpots = false;
    for(const [index,item]of items.entries()){
        const operate=async(operation,extra={})=>{const fresh=await request('/lobbies/characters/'+owner.id,null,token,'GET');
            return request('/lobbies/characters/'+owner.id+'/portable-lights',{operation,path:['inventory','pockets',index],source_id:item.id,
                character_revision:fresh.data._revision,...extra},token);};
        if(index===0){
            await measure('off');
            const reservedSlots="FG.scene.children.filter(light=>light.isSpotLight&&light.decay<0&&light.intensity===0)";
            reservedSpots = await page.evaluate(`${reservedSlots}.filter(light=>light.visible).length===4`);
            if(reservedSpots){
                await page.evaluate(`${reservedSlots}.forEach(light=>{light.visible=false});true`);
                try{await measure('off-without-reserved',true);}
                finally{await page.evaluate(`${reservedSlots}.forEach(light=>{light.visible=true});true`);}
                await measure('off-reserved-repeat',true);
            }
        }
        const off=await sample();await shot('lamp-'+index+'-off');
        await page.evaluate('(()=>{window.lampFrameGaps=[];window.lampFrameLast=performance.now();window.lampFrameGeneration=(window.lampFrameGeneration||0)+1;const generation=lampFrameGeneration;function lampFrame(t){if(generation!==lampFrameGeneration)return;lampFrameGaps.push(t-lampFrameLast);lampFrameLast=t;if(lampFrameGaps.length<5000)requestAnimationFrame(lampFrame)};requestAnimationFrame(lampFrame);return true})()');
        await operate('prepare',{mount:index===0?'head':'hand'});const programsBefore=await page.evaluate('FG.renderer.info.programs.length'),onStarted=Date.now();await operate('toggle');
        await page.until("FG.scene.children.some(l=>l.isSpotLight&&Math.abs(l.position.x-2.5)<.1&&Math.abs(l.position.z-30.85)<.1)",'Настоящий луч включён');
        const on=await sample();const onMs=Date.now()-onStarted;const onGap=await page.evaluate('Math.max(0,...lampFrameGaps)');assert.ok(onGap<1000,item.name+': зависание кадра '+onGap);const programsAfter=await page.evaluate('FG.renderer.info.programs.length');await shot('lamp-'+index+'-on');
        if(index===0)await measure('on');
        fs.writeFileSync(path.join(safeSession.artifacts,'beam-probes-'+index+'.json'),JSON.stringify({off,on},null,2));
        for(const distance of [1.5,3,6,12,15,18,19,19.7]){
            const delta=on.find(p=>p.distance===distance&&p.lateral===0).brightness-off.find(p=>p.distance===distance&&p.lateral===0).brightness;
            // На краю луч уже ослаблен, а рельеф дороги меняет яркость отдельных пикселей.
            assert.ok(delta>(distance>=19?2:5),item.name+': нет света на '+distance+' м ('+delta.toFixed(2)+')');
        }
        for(const probe of on.filter(p=>p.lateral)){
            const original=off.find(p=>p.distance===probe.distance&&p.lateral===probe.lateral);
            assert.ok(Math.abs(probe.brightness-original.brightness)<2,item.name+': свет вышел за прежнюю ширину');
        }
        assert.ok(on.find(p=>p.distance===20.2&&!p.lateral).brightness-off.find(p=>p.distance===20.2&&!p.lateral).brightness>2,'Центр округлого конца дальше бокового края');
        assert.ok(on.find(p=>p.distance===22&&!p.lateral).brightness-off.find(p=>p.distance===22&&!p.lateral).brightness<2,'За 20 метрами света нет');
        const beams=await page.evaluate("FG.scene.children.filter(l=>l.isSpotLight&&Math.abs(l.position.x-2.5)<.1).map(l=>({angle:l.angle,range:l.distance,height:l.position.y,aspect:l.shadow.camera.aspect}))");
        assert.equal(beams.length,1);assert.equal(beams[0].range,20);
        const emitterHeight=await page.evaluate(`(()=>{const lamp=FG.scene.children.find(node=>node.isSpotLight&&node.intensity>0);
            return lamp.position.y-FG.characters.get(${owner.id}).model.position.y})()`);
        assert.ok(Math.abs(emitterHeight-(index===0?1.65:1.2))<.001,'Высота начала луча');
        assert.ok(!await page.evaluate('FG.scene.children.some(node=>node.userData.portableEmitter)'), 'В воздухе нет маркера');
        if(index<2){
            await page.evaluate('locationControls.target.set(2.5,2.2,31);locationControls.object.position.set(-.5,4.5,30);locationControls.update();true');
            await shot(index===0?'emitter-close-head':'emitter-close-hand');
            await page.evaluate('locationControls.target.set(2.5,1,39);locationControls.object.position.set(-7,12,31);locationControls.update();true');
        }
        if(index===0){
            await page.evaluate('locationControls.object.position.set(-10,16,35);locationControls.update();true');
            const angleOn=await sample();await shot('lamp-head-second-angle');
            await operate('toggle');await page.until("!FG.scene.children.some(l=>l.isSpotLight&&Math.abs(l.position.x-2.5)<.1)",'Фонарь выключен');
            const angleOff=await sample();assert.ok(angleOn[1].brightness-angleOff[1].brightness>5,'Ближний свет не зависит от ракурса');
            await page.evaluate('locationControls.object.position.set(-7,12,31);locationControls.update();true');await operate('toggle');
            await page.until("FG.scene.children.some(l=>l.isSpotLight&&Math.abs(l.position.x-2.5)<.1)",'Фонарь включён повторно');
        }
        const offStarted=Date.now();await page.evaluate('lampFrameGaps=[];true');await operate('stow');
        await page.until("!FG.scene.children.some(l=>l.isSpotLight&&Math.abs(l.position.x-2.5)<.1&&Math.abs(l.position.z-30.85)<.1)",'Источник убран');const offMs=Date.now()-offStarted,offGap=await page.evaluate('Math.max(0,...lampFrameGaps)');
        assert.ok(offGap<1000,item.name+': зависание при выключении '+offGap);rows.push({name:item.name,off,on,beams,onMs,onGap,offMs,offGap,programsBefore,programsAfter});
    }
    const fresh=await request('/lobbies/characters/'+owner.id,null,token,'GET');
    await request('/lobbies/characters/'+owner.id+'/portable-lights',{operation:'prepare',path:['inventory','pockets',0],source_id:items[0].id,
        character_revision:fresh.data._revision,mount:'head'},token);
    const next=await request('/lobbies/characters/'+owner.id,null,token,'GET');
    await request('/lobbies/characters/'+owner.id+'/portable-lights',{operation:'toggle',path:['inventory','pockets',0],source_id:items[0].id,
        character_revision:next.data._revision},token);
    const wall=await request(loc+'/objects',{type:'wall',name:'Проверка тени фонаря',tile_x:2,tile_y:35,
        properties:{rotation:0,dimensions:{width:4,depth:.25,height:3},level:0}},token);
    await page.until(`FG.roots.some(mesh=>mesh.userData.locationObjectId===${wall.id})`,'Настоящая стена в Припяти');
    const blocked=await sample();await shot('lamp-wall-shadow');
    assert.ok(blocked.find(p=>p.distance===3&&!p.lateral).brightness-rows[0].off[1].brightness>5,'До стены свет сохраняется');
    assert.ok(blocked.find(p=>p.distance===6&&!p.lateral).brightness-rows[0].off[2].brightness<5,'Стена перекрывает свет');
    fs.writeFileSync(path.join(safeSession.artifacts,'wall-shadow.json'),JSON.stringify(blocked,null,2));
    fs.writeFileSync(path.join(safeSession.artifacts,'nearfield.json'),JSON.stringify(rows,null,2));
    fs.writeFileSync(path.join(safeSession.artifacts,'light-performance.json'),JSON.stringify(performanceRows,null,2));
    const day=await request(loc,null,token,'GET');
    await request(loc,{lighting:{...day.lighting,mode:'manual',ambient_intensity:.7,directional_intensity:.85,background_color:'#a8b4b8'}},token,'PUT');
    await page.evaluate('locationControls.target.set(36,1,36);locationControls.object.position.set(86,49,86);locationControls.update();true');
    await shot('day-terrain-after-lights');
    const errors=await page.evaluate('FG.renderer.getContext().getError()');assert.equal(errors,0,'Ошибка графики после переключений');
    await page.evaluate(`window.openCharacterSheet(${owner.id},'equipment').then(()=>true)`);
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=detector]').click();true");
    for(const[width,height]of [[1920,1080],[1600,900],[1366,768],[1280,720]]){
        await page.call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
        await page.until("[...document.querySelectorAll('.portable-light-options')].filter(group=>group.offsetWidth>0).length===5",
            'Батарейки и крепления видны после обновления вкладки «Приборы»');
        const groups=await page.evaluate(`(()=>{const box=e=>{const r=e.getBoundingClientRect();return{x:r.x,y:r.y,right:r.right,bottom:r.bottom}};
            return [...document.querySelectorAll('.portable-light-options')].map(group=>({
                select:box(group.querySelector('select')),button:box(group.querySelector('button')),group:box(group),
                caption:box(group.parentElement.querySelector('strong')),label:group.querySelector('select').getAttribute('aria-label')}))})()`);
        assert.equal(groups.length,5);
        for(const group of groups){
            assert.ok(group.group.y>=group.caption.bottom,'Выбор ниже названия: '+group.label);
            assert.ok(group.select.right<=group.button.x,'Выбор рядом с кнопкой: '+group.label);
            assert.ok(Math.min(group.select.bottom,group.button.bottom)>Math.max(group.select.y,group.button.y),'Одна строка: '+group.label);
            assert.ok(group.button.right<=group.group.right+1&&group.group.right<=width,'Без горизонтального переполнения');
        }
        if(width===1920||width===1280)await shot('lamp-controls-'+width);
    }
    await page.call('Emulation.setDeviceMetricsOverride',{width:1920,height:1080,deviceScaleFactor:1,mobile:false});
    const sheetBox=()=>page.evaluate("(()=>{const r=document.querySelector('#character-sheet-modal .modal-content').getBoundingClientRect();return{x:r.x,y:r.y,width:r.width}})()");
    const beforeDrag=await sheetBox(),pointer={x:beforeDrag.x+beforeDrag.width*.5,y:beforeDrag.y+30};
    await page.call('Input.dispatchMouseEvent',{type:'mousePressed',...pointer,button:'left',clickCount:1});
    await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',x:pointer.x+90,y:pointer.y+60,button:'left',buttons:1});
    await page.call('Input.dispatchMouseEvent',{type:'mouseReleased',x:pointer.x+90,y:pointer.y+60,button:'left',clickCount:1});
    const afterDrag=await sheetBox();assert.equal(afterDrag.x,beforeDrag.x);assert.equal(afterDrag.y,beforeDrag.y);
    const multipleLights = {reservedSpots,textureUnits:await page.evaluate('FG.renderer.capabilities.maxTextures')};
    const saveMultipleLights = stage => {
        multipleLights.stage = stage;
        fs.writeFileSync(path.join(safeSession.artifacts,'four-lamps.json'),JSON.stringify(multipleLights,null,2));
    };
    await page.evaluate(`(()=>{
        const gl=FG.renderer.getContext();
        const samples={programQueries:[],renders:[]};window.multipleLampSamples=samples;
        for(const method of ['getProgramParameter','getProgramInfoLog','getActiveUniform','linkProgram']){
            const original=gl[method].bind(gl);
            gl[method]=(...args)=>{
                const start=performance.now();
                try{return original(...args)}finally{
                    const ms=performance.now()-start;if(ms>25)samples.programQueries.push({method,ms});
                }
            };
        }
        const render=FG.renderer.render;
        FG.renderer.render=function(...args){
            const start=performance.now(),lights=L.getLocationPortableLightDiagnostics().lights;
            try{return render.apply(this,args)}finally{
                const ms=performance.now()-start;if(ms>25)samples.renders.push({lights,ms});
            }
        };
        return true;
    })()`);
    saveMultipleLights('Включение второго фонаря первого персонажа');
    const lampRow=index=>`document.querySelector('.portable-light-row[data-light-id="nearfield-${index}"]')`;
    await page.evaluate(`${lampRow(1)}.querySelector('[data-light-operation=prepare]').click();true`);
    await page.until(`${lampRow(1)}?.textContent.includes('в руке')`,'Обычный фонарь подготовлен');
    await page.evaluate(`${lampRow(1)}.querySelector('[data-light-operation=toggle]').click();true`);
    await page.until(`${lampRow(1)}?.textContent.includes('включён')`,'Обычный фонарь включён');
    await page.evaluate(`${lampRow(2)}.querySelector('[data-light-operation=prepare]').click();true`);
    await page.until(`${lampRow(2)}?.textContent.includes('в руке')&&${lampRow(2)}?.textContent.includes('включён')&&!${lampRow(1)}?.textContent.includes('в руке')`,'Замена включённого фонаря одной кнопкой');
    const replaced=await request('/lobbies/characters/'+owner.id,null,token,'GET');
    assert.deepEqual(replaced.data.health.combatMeta.portableLights.filter(entry=>entry.mount==='hand').map(entry=>[entry.id,entry.on]),[['nearfield-2',true]]);
    await shot('lamp-replaced-with-one-click');
    multipleLights.samples=await page.evaluate('window.multipleLampSamples');
    saveMultipleLights('Два включённых источника');
    const helperItems=items.slice(0,2).map((item,index)=>({...item,id:'second-source-'+index}));
    const helper=await request(`/lobbies/${lobby.id}/characters`,{name:'Второй носитель света',data:{inventory:{pockets:helperItems,backpack:[]}}},token);
    await request(loc+'/spawn_character',{character_id:helper.id,tile_x:7,tile_y:30},token);
    saveMultipleLights('Включение источников второго персонажа');
    for(const [index,item] of helperItems.entries())for(const operation of ['prepare','toggle']){
        const state=await request('/lobbies/characters/'+helper.id,null,token,'GET');
        await request('/lobbies/characters/'+helper.id+'/portable-lights',{operation,path:['inventory','pockets',index],source_id:item.id,
            character_revision:state.data._revision,...(operation==='prepare'?{mount:index===0?'head':'hand'}:{})},token);
    }
    saveMultipleLights('Камера полного обзора');
    await page.evaluate('window.closeCharacterSheet();locationControls.target.set(36,1,36);locationControls.object.position.set(86,49,86);locationControls.update();true');
    saveMultipleLights('Четыре включённых источника');
    await page.until('L.getLocationPortableLightDiagnostics().lights===4','Четыре настоящих включённых источника');
    multipleLights.samples=await page.evaluate('window.multipleLampSamples');
    saveMultipleLights('Проверка шейдеров');
    const shaderErrors=await page.evaluate(`(async()=>{
        await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
        return FG.renderer.info.programs.filter(program=>program.diagnostics?.runnable===false)
            .map(program=>program.diagnostics.programLog.slice(0,200));
    })()`);
    assert.deepEqual(shaderErrors,[],'Несколько фонарей не ломают материалы');
    await shot('four-lamps-terrain');
    multipleLights.samples=await page.evaluate('window.multipleLampSamples');
    if(reservedSpots)assert.ok(multipleLights.samples.renders.every(({ms})=>ms<1000),
        'Переключение нескольких фонарей не должно останавливать отрисовку');
    saveMultipleLights('Готово');
    console.log('Ближний свет: три фонаря, настоящая ночная Припять, одинаковые камера и пробы земли; результат сохранён.');
};
