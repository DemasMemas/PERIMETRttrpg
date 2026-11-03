const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');

module.exports=async({page,request,token,lobby,location,safeSession})=>{
    const url='/lobbies/'+lobby.id+'/locations/'+location.id;
    const anomaly=await request(url+'/objects',{name:'Проверочная Баня',type:'anomaly',tile_x:41,tile_y:48,
        properties:{anomaly_key:'banya',anomalyType:'fire',dimensions:{width:1,depth:1,height:1}}},token);
    await page.evaluate('window.labelState=()=>{let result;L.sceneGraphicsForTest().scene.traverse(node=>{if(node.userData.anomalyLabelText==="Проверочная Баня"){const s=node.getWorldScale(new T.Vector3());result={visible:node.visible,width:s.x,height:s.y,depthTest:node.material.depthTest}}});return result};window.setTestCamera=(x,y,z,tx,ty,tz)=>{const g=L.sceneGraphicsForTest();g.controls.target.set(tx,ty,tz);g.camera.position.set(x,y,z);g.controls.update();g.camera.updateMatrixWorld(true)}');
    await page.evaluate('setTestCamera(41.5,9,58.5,41.5,1,48.5)');
    await page.until('labelState()?.visible','метка открытой аномалии видна');
    await page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    const before=await page.evaluate('labelState()');assert.ok(before.depthTest);
    await request('/lobbies/'+lobby.id+'/locations/objects/'+anomaly.id,{properties:{dimensions:{width:6,depth:4,height:1}}},token,'PATCH');
    await page.until('(()=>{const root=L.sceneGraphicsForTest().roots.find(r=>r.userData.locationObject?.id==='+anomaly.id+');return root?.userData.locationObject.properties.dimensions.width===6})()','аномалия увеличена до шести метров');
    await page.until('labelState()?.visible','метка после изменения размера');
    await page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    const enlarged=await page.evaluate('labelState()');
    assert.ok(Math.abs(before.width-enlarged.width)<1e-5&&Math.abs(before.height-enlarged.height)<1e-5,'размер текста не наследует масштаб аномалии: '+JSON.stringify({before,enlarged}));
    const screenshot=async name=>fs.writeFileSync(path.join(safeSession.artifacts,name+'.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    await screenshot('anomaly-label-large');
    const wall=await request(url+'/objects',{name:'Перекрытие метки стеной',type:'wall',tile_x:41,tile_y:53,
        properties:{dimensions:{width:8,depth:.2,height:5}}},token);
    await page.until('labelState()?.visible===false','метка скрыта, когда аномалия за стеной');
    await screenshot('anomaly-label-occluded');
    await request('/lobbies/'+lobby.id+'/locations/objects/'+wall.id,null,token,'DELETE');
    await page.until('labelState()?.visible','метка возвращается после удаления укрытия');
    await page.evaluate('setTestCamera(10,12,22,10,1,10)');
    await page.until('labelState()?.visible===false','метка скрыта за пределами камеры');
    const current=(await request(url,null,token,'GET')).objects.find(object=>object.id===anomaly.id);
    await page.evaluate('L.showObjectContextMenu(600,300,'+JSON.stringify(current)+')');
    assert.equal(await page.evaluate('[...document.querySelectorAll("button")].some(button=>button.textContent.startsWith("Класс укрытия:"))'),false);
    const inspect=await page.evaluate('(()=>{const b=[...document.querySelectorAll("button")].find(button=>button.textContent==="Осмотреть"),r=b.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()');
    for(const type of ['mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{type,...inspect,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1});
    await page.until('[...document.querySelectorAll(".notification")].some(n=>n.textContent==="Проверочная Баня · Аномалия")','осмотр аномалии без выдуманных ОЗ и защиты');
    const performanceViews=[];
    for(const view of [{name:'overview',target:[36,1,36],radius:32,height:30},{name:'street',target:[41,1,48],radius:18,height:10}]){
        for(const enabled of [false,true]){
            const measurement=await page.evaluate(`(async()=>{
                const g=L.sceneGraphicsForTest(),view=${JSON.stringify(view)};
                const pose=i=>{const a=.65+i*.025;g.controls.target.set(view.target[0]+Math.sin(a)*3,view.target[1],view.target[2]+Math.cos(a)*3);g.camera.position.copy(g.controls.target).add(new T.Vector3(Math.sin(a)*view.radius,view.height,Math.cos(a)*view.radius));g.controls.update();};
                pose(0);for(let i=0;i<20;i++)await new Promise(requestAnimationFrame);
                L.beginAnomalyLabelProfileForTest(${enabled});
                const frames=[];let previous;
                for(let i=0;i<120;i++)await new Promise(resolve=>requestAnimationFrame(t=>{if(previous)frames.push(t-previous);previous=t;pose(i);resolve();}));
                const labels=L.endAnomalyLabelProfileForTest();
                const stats=values=>{values.sort((a,b)=>a-b);return{count:values.length,median:values[Math.floor(values.length*.5)],p95:values[Math.floor(values.length*.95)],max:values.at(-1),total:values.reduce((a,b)=>a+b,0)}};
                return{frameMs:stats(frames),framesOver33Ms:frames.filter(t=>t>33).length,labelUpdateMs:stats(labels),expensiveLabelUpdates:labels.filter(t=>t>1).length};
            })()`);
            performanceViews.push({view:view.name,enabled,...measurement});
        }
    }
    fs.writeFileSync(path.join(safeSession.artifacts,'anomaly-camera-performance.json'),JSON.stringify(performanceViews,null,2));
    console.log('ANOMALY_CAMERA '+JSON.stringify(performanceViews));
    const boars=[];
    for(const [index,x] of [[0,41],[1,43]]){
        const boar=await request('/lobbies/'+lobby.id+'/characters',{name:'Кабан GLB '+index,data:{is_mutant:true,basic:{mutant_type:'Кабан'},mutant:{profile:'Кабан'},health:{current:700,max:700,effects:[]}}},token);
        boars.push(boar);
        await request(url+'/spawn_character',{character_id:boar.id,tile_x:x,tile_y:44},token);
        await page.until('L.sceneGraphicsForTest().characters.get('+boar.id+')?.model.userData.importedModelReady','кабан загружен из GLB');
    }
    const inspectModel=id=>page.evaluate('(()=>{const root=L.sceneGraphicsForTest().characters.get('+id+').model,body=[];root.traverse(mesh=>{if(!mesh.isMesh||!mesh.material)return;const materials=Array.isArray(mesh.material)?mesh.material:[mesh.material];for(const m of materials)if(m.map)body.push({geometry:mesh.geometry.uuid,material:m.uuid,width:m.map.image?.width,height:m.map.image?.height,normal:Boolean(m.normalMap)})});return body})()');
    const first=await inspectModel(boars[0].id),second=await inspectModel(boars[1].id);
    assert.ok(first.length>=2&&first.every(material=>material.width>0&&material.height>0),'обе поверхности кабана с загруженными текстурами');
    assert.ok(first.some(material=>material.normal),'карта нормалей подключена');
    assert.equal(first[0].geometry,second[0].geometry,'геометрия переиспользуется');
    assert.notEqual(first[0].material,second[0].material,'материалы экземпляров независимы');
    await page.evaluate('setTestCamera(46,5,50,42,1.5,44.5)');
    await page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    await screenshot('boar-front');
    await page.evaluate('setTestCamera(37,4,44,42,1.5,44.5)');
    await page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    await screenshot('boar-side');
    const pixels=await page.evaluate('(async()=>{const g=L.sceneGraphicsForTest();return {drawCalls:g.renderer.info.render.calls,geometries:g.renderer.info.memory.geometries,textures:g.renderer.info.memory.textures}})()');
    await page.evaluate('L.openCharacterAttackSelection('+boars[0].id+')');
    await page.until('document.querySelector(".mutant-attack-close svg")','крестик атак с иконкой общего стиля');
    const closeStyle=await page.evaluate('(()=>{const b=document.querySelector(".mutant-attack-close"),s=getComputedStyle(b),r=b.getBoundingClientRect();return{background:s.backgroundColor,color:s.color,borderRadius:s.borderRadius,width:r.width,height:r.height,label:b.getAttribute("aria-label"),x:r.x+r.width/2,y:r.y+r.height/2}})()');
    assert.equal(closeStyle.width,36);assert.equal(closeStyle.height,36);assert.equal(closeStyle.borderRadius,'50%');assert.ok(closeStyle.label);
    await screenshot('mutant-attack-close');
    for(const type of ['mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{type,x:closeStyle.x,y:closeStyle.y,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1});
    await page.until('!document.querySelector(".mutant-attack-close")','крестик закрыл окно атак');
    fs.writeFileSync(path.join(safeSession.artifacts,'anomaly-boar.json'),JSON.stringify({scene:'копия Припяти',labelBefore:before,labelEnlarged:enlarged,hiddenBehindWall:true,hiddenOutsideCamera:true,noCoverStatsOnAnomaly:true,boarTextured:true,boarNormalMap:true,twoInstancesShareGeometry:true,mutantAttackClose:closeStyle,first,second,pixels},null,2));
    await require('./boar_render_performance.browser.cjs')({page,safeSession,boars});
};
