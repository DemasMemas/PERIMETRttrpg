const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');

module.exports=async({page,request,token,lobby,location,safeSession})=>{
    const url='/lobbies/'+lobby.id+'/locations/'+location.id;
    const anomaly=await request(url+'/objects',{name:'Проверочная Баня',type:'anomaly',tile_x:41,tile_y:48,
        properties:{anomaly_key:'banya',anomalyType:'fire',dimensions:{width:1,depth:1,height:1}}},token);
    await page.evaluate('window.setTestCamera=(x,y,z,tx,ty,tz)=>{const g=L.sceneGraphicsForTest();g.controls.target.set(tx,ty,tz);g.camera.position.set(x,y,z);g.controls.update();g.camera.updateMatrixWorld(true)}');
    await page.evaluate('setTestCamera(41.5,9,58.5,41.5,1,48.5)');
    const visual = 'L.sceneGraphicsForTest().roots.find(root=>root.userData.locationObject?.id===' + anomaly.id + ')';
    await page.until(visual + '?.children.some(node=>node.userData.anomalyEffect)', 'видна модель аномалии');
    const hasAnomalyLabel = '(()=>{let label=false;' + visual + '.traverse(node=>{label ||= node.isSprite===true});return label})()';
    assert.equal(await page.evaluate(hasAnomalyLabel), false, 'У аномалии нет подписи');
    await request('/lobbies/'+lobby.id+'/locations/objects/'+anomaly.id,{properties:{dimensions:{width:6,depth:4,height:1}}},token,'PATCH');
    await page.until(visual + '?.userData.locationObject.properties.dimensions.width===6', 'аномалия увеличена до шести метров');
    assert.equal(await page.evaluate(hasAnomalyLabel), false, 'При перестроении подпись не появляется');
    const screenshot=async name=>fs.writeFileSync(path.join(safeSession.artifacts,name+'.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    const current=(await request(url,null,token,'GET')).objects.find(object=>object.id===anomaly.id);
    await page.evaluate('L.showObjectContextMenu(600,300,'+JSON.stringify(current)+')');
    assert.equal(await page.evaluate('[...document.querySelectorAll("button")].some(button=>button.textContent.startsWith("Класс укрытия:"))'),false);
    const inspect=await page.evaluate('(()=>{const b=[...document.querySelectorAll("button")].find(button=>button.textContent==="Осмотреть"),r=b.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()');
    for(const type of ['mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{type,...inspect,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1});
    await page.until('[...document.querySelectorAll(".notification")].some(n=>n.textContent==="Проверочная Баня · Аномалия")','осмотр аномалии без выдуманных ОЗ и защиты');
    const boars=[];
    for(const [index,x] of [[0,41],[1,43]]){
        const boar=await request('/lobbies/'+lobby.id+'/characters',{name:'Кабан GLB '+index,data:{is_mutant:true,basic:{mutant_type:'Кабан'},mutant:{profile:'Кабан'},health:{current:700,max:700,effects:[]}}},token);
        boars.push(boar);
        await request(url+'/spawn_character',{character_id:boar.id,tile_x:x,tile_y:44},token);
        await page.until('L.sceneGraphicsForTest().characters.get('+boar.id+')?.model.userData.importedModelReady','кабан загружен из GLB');
    }
    const inspectModel=id=>page.evaluate('(()=>{const root=L.sceneGraphicsForTest().characters.get('+id+').model,body=[];root.traverse(mesh=>{if(mesh.isMesh)body.push({geometry:mesh.geometry.uuid,material:mesh.material.uuid,triangles:mesh.geometry.index?.count/3,colors:Boolean(mesh.geometry.attributes.color)})});return body})()');
    const first=await inspectModel(boars[0].id),second=await inspectModel(boars[1].id);
    assert.ok(first.length===1&&first[0].colors&&first[0].triangles===2308,'лёгкий кабан загружен одной сеткой');
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
    fs.writeFileSync(path.join(safeSession.artifacts,'anomaly-boar.json'),JSON.stringify({scene:'копия Припяти',anomalyHasNoLabel:true,noCoverStatsOnAnomaly:true,boarVertexColors:true,twoInstancesShareGeometry:true,mutantAttackClose:closeStyle,first,second,pixels},null,2));
    await require('./boar_render_performance.browser.cjs')({page,safeSession,boars});
};
