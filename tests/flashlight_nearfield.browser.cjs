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
        for(const [distance,lateral] of [[1.5,0],[3,0],[6,0],[12,0],[15,0],[6,2],[15,3],[22,0]]){const point=new T.Vector3(2.5+lateral,ground+.025,30.5+distance).project(camera);
            const x=Math.round((point.x+1)*.5*gl.drawingBufferWidth),y=Math.round((point.y+1)*.5*gl.drawingBufferHeight),pixels=new Uint8Array(5*5*4);
            if(x<3||y<3||x>=gl.drawingBufferWidth-3||y>=gl.drawingBufferHeight-3)throw Error('Проба вне кадра');
            gl.readPixels(x-2,y-2,5,5,gl.RGBA,gl.UNSIGNED_BYTE,pixels);let brightness=0;for(let i=0;i<pixels.length;i+=4)brightness+=(pixels[i]+pixels[i+1]+pixels[i+2])/3;
            out.push({distance,lateral,brightness:brightness/25});}return out})()`);
    const shot=async name=>fs.writeFileSync(path.join(safeSession.artifacts,name+'.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    const rows=[];
    for(const [index,item]of items.entries()){
        const operate=async(operation,extra={})=>{const fresh=await request('/lobbies/characters/'+owner.id,null,token,'GET');
            return request('/lobbies/characters/'+owner.id+'/portable-lights',{operation,path:['inventory','pockets',index],source_id:item.id,
                character_revision:fresh.data._revision,...extra},token);};
        const off=await sample();await shot('lamp-'+index+'-off');
        await page.evaluate('(()=>{window.lampFrameGaps=[];window.lampFrameLast=performance.now();window.lampFrameGeneration=(window.lampFrameGeneration||0)+1;const generation=lampFrameGeneration;function lampFrame(t){if(generation!==lampFrameGeneration)return;lampFrameGaps.push(t-lampFrameLast);lampFrameLast=t;if(lampFrameGaps.length<5000)requestAnimationFrame(lampFrame)};requestAnimationFrame(lampFrame);return true})()');
        await operate('prepare',{mount:index===0?'head':'hand'});const programsBefore=await page.evaluate('FG.renderer.info.programs.length'),onStarted=Date.now();await operate('toggle');
        await page.until("FG.scene.children.some(l=>l.isSpotLight&&Math.abs(l.position.x-2.5)<.1&&Math.abs(l.position.z-30.85)<.1)",'Настоящий луч включён');
        const on=await sample();const onMs=Date.now()-onStarted;const onGap=await page.evaluate('Math.max(0,...lampFrameGaps)');assert.ok(onGap<1000,item.name+': зависание кадра '+onGap);const programsAfter=await page.evaluate('FG.renderer.info.programs.length');await shot('lamp-'+index+'-on');
        for(const distance of [1.5,3,6,12,15]){
            const delta=on.find(p=>p.distance===distance&&p.lateral===0).brightness-off.find(p=>p.distance===distance&&p.lateral===0).brightness;
            assert.ok(delta>5,item.name+': нет света на '+distance+' м');
        }
        for(const probe of on.filter(p=>p.lateral)){
            const original=off.find(p=>p.distance===probe.distance&&p.lateral===probe.lateral);
            assert.ok(Math.abs(probe.brightness-original.brightness)<2,item.name+': свет вышел за прежнюю ширину');
        }
        assert.ok(on.find(p=>p.distance===22&&!p.lateral).brightness-off.find(p=>p.distance===22&&!p.lateral).brightness<2,'За 20 метрами света нет');
        const beams=await page.evaluate("FG.scene.children.filter(l=>l.isSpotLight&&Math.abs(l.position.x-2.5)<.1).map(l=>({angle:l.angle,range:l.distance,height:l.position.y,aspect:l.shadow.camera.aspect}))");
        assert.equal(beams.length,1);assert.equal(beams[0].range,20);
        const emitterHeight=await page.evaluate(`(()=>{const lamp=FG.scene.children.find(node=>node.userData.portableEmitter&&node.visible);
            return lamp.position.y-FG.characters.get(${owner.id}).model.position.y})()`);
        assert.ok(Math.abs(emitterHeight-(index===0?1.65:1.2))<.001,'Высота видимого источника');
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
    const day=await request(loc,null,token,'GET');
    await request(loc,{lighting:{...day.lighting,mode:'manual',ambient_intensity:.7,directional_intensity:.85,background_color:'#a8b4b8'}},token,'PUT');
    await page.evaluate('locationControls.target.set(36,1,36);locationControls.object.position.set(86,49,86);locationControls.update();true');
    await shot('day-terrain-after-lights');
    const errors=await page.evaluate('FG.renderer.getContext().getError()');assert.equal(errors,0,'Ошибка графики после переключений');
    await page.evaluate(`window.openCharacterSheet(${owner.id},'equipment').then(()=>true)`);
    await page.until("document.querySelectorAll('.portable-light-options').length===5",'Батарейки и крепление в нижних строках');
    for(const[width,height]of [[1920,1080],[1600,900],[1366,768],[1280,720]]){
        await page.call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
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
    console.log('Ближний свет: три фонаря, настоящая ночная Припять, одинаковые камера и пробы земли; результат сохранён.');
};
