// Три настоящих фонаря в ночной Припяти: один ракурс, выключено/включено.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,offered,loc,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'lights');assert.ok(safeSession.verified);
    await page.call('Emulation.setDeviceMetricsOverride',{width:1280,height:720,deviceScaleFactor:1,mobile:false});
    const templates=['Налобный фонарик','Фонарик','Улучшенный фонарик'].map(name=>{
        const item=offered.global.find(t=>t.name===name&&t.category!=='weapon_module');assert.ok(item,name);return item;});
    const items=templates.map((t,index)=>({id:'nearfield-'+index,templateId:t.id,name:t.name,category:t.category,
        quantity:1,weight:t.weight,volume:t.volume,attributes:structuredClone(t.attributes||{})}));
    const owner=await request(`/lobbies/${lobby.id}/characters`,{name:'Проверка ближнего света',data:{inventory:{pockets:items,backpack:[]}}},token);
    await request(loc+'/spawn_character',{character_id:owner.id,tile_x:2,tile_y:30},token);
    await request(loc+'/characters/'+owner.id+'/facing',{facing_x:0,facing_y:1},token,'PATCH');
    const detail=await request(loc,null,token,'GET');
    await request(loc,{lighting:{...detail.lighting,mode:'manual',ambient_intensity:.03,directional_intensity:0,background_color:"#0b1018"}},token,'PUT');
    await page.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');window.T=await import('three');window.FG=L.sceneGraphicsForTest();
        locationControls.enableDamping=false;locationControls.target.set(2.5,1,39);locationControls.object.position.set(-7,12,31);locationControls.update();return true})()`);
    const sample=()=>page.evaluate(`(()=>{const {renderer,scene,camera}=FG;renderer.render(scene,camera);const gl=renderer.getContext(),out=[];
        const ground=FG.characters.get(${owner.id}).model.position.y;
        for(const [distance,lateral] of [[1.5,0],[3,0],[6,0],[12,0],[20,0],[6,1],[20,2]]){const point=new T.Vector3(2.5+lateral,ground+.025,30.5+distance).project(camera);
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
        await operate('prepare',{mount:index===0?'head':'hand'});await operate('toggle');
        await page.until("FG.scene.children.some(l=>l.isSpotLight&&Math.abs(l.position.x-2.5)<.1&&Math.abs(l.position.z-30.85)<.1)",'Настоящий луч включён');
        const on=await sample();await shot('lamp-'+index+'-on');
        for(const distance of [1.5,3,6,12,20]){
            const delta=on.find(p=>p.distance===distance&&p.lateral===0).brightness-off.find(p=>p.distance===distance&&p.lateral===0).brightness;
            assert.ok(delta>5,item.name+': нет света на '+distance+' м');
        }
        for(const probe of on.filter(p=>p.lateral)){
            const original=off.find(p=>p.distance===probe.distance&&p.lateral===probe.lateral);
            assert.ok(Math.abs(probe.brightness-original.brightness)<2,item.name+': свет вышел за прежнюю ширину');
        }
        const beams=await page.evaluate("FG.scene.children.filter(l=>l.isSpotLight&&Math.abs(l.position.x-2.5)<.1).map(l=>({angle:l.angle,range:l.distance,height:l.position.y,aspect:l.shadow.camera.aspect}))");
        if(index===0){
            await page.evaluate('locationControls.object.position.set(-10,16,35);locationControls.update();true');
            const angleOn=await sample();await shot('lamp-head-second-angle');
            await operate('toggle');await page.until("!FG.scene.children.some(l=>l.isSpotLight&&Math.abs(l.position.x-2.5)<.1)",'Фонарь выключен');
            const angleOff=await sample();assert.ok(angleOn[1].brightness-angleOff[1].brightness>5,'Ближний свет не зависит от ракурса');
            await page.evaluate('locationControls.object.position.set(-7,12,31);locationControls.update();true');await operate('toggle');
            await page.until("FG.scene.children.some(l=>l.isSpotLight&&Math.abs(l.position.x-2.5)<.1)",'Фонарь включён повторно');
        }
        rows.push({name:item.name,off,on,beams});await operate('stow');
        await page.until("!FG.scene.children.some(l=>l.isSpotLight&&Math.abs(l.position.x-2.5)<.1&&Math.abs(l.position.z-30.85)<.1)",'Источник убран');
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
    console.log('Ближний свет: три фонаря, настоящая ночная Припять, одинаковые камера и пробы земли; результат сохранён.');
};
