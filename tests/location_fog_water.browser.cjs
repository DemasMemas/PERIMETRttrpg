const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {spawnSync}=require('node:child_process');

module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession})=>{
    const root=path.join(__dirname,'..');
    const python=path.join(root,'.venv','Scripts','python.exe');
    const code=`import sys,json;sys.path.insert(0,${JSON.stringify(root)});from scripts.seed_visual_test_location import _stress_tiles;print(json.dumps(_stress_tiles()))`;
    const generated=spawnSync(python,['-I','-B','-c',code],{cwd:root,encoding:'utf8',maxBuffer:8*1024*1024});
    assert.equal(generated.status,0,generated.stderr);
    const original=JSON.parse(generated.stdout);
    const deep=structuredClone(original);
    for(let z=34;z<56;z++)for(let x=38;x<64;x++){
        const water=(x%4)<2&&(z%4)<2;
        deep[z][x]={...deep[z][x],terrain:water?'water':'grass',height:water ? .5 : 3,objects:[]};
    }
    const api=`/lobbies/${lobby.id}`;
    const location=await request(api+'/locations',{name:'Визуальный полигон 2 — копия проверки тумана',
        world_tile_x:12,world_tile_z:7,grid_width:64,grid_height:56,tiles_data:original},token);
    const url=api+'/locations/'+location.id;
    const playerPassword='fog-test-password';
    await request('/auth/register',{username:'fog-player',email:'fog-player@example.test',password:playerPassword});
    const login=await request('/auth/login',{username:'fog-player',password:playerPassword});
    const playerToken=login.access_token;
    const playerId=Number(JSON.parse(Buffer.from(playerToken.split('.')[1],'base64url')).sub);
    await request(api+'/join',{},playerToken);
    const actor=await request(api+'/characters',{name:'Наблюдатель тумана',data:{}},playerToken);
    await request(url+'/spawn_character',{character_id:actor.id,tile_x:50,tile_y:47,assign_to_user_id:playerId},token);
    const objects=await request(url+'/objects/batch',{objects:[
        {name:'Скрытый контрольный ящик',type:'box',tile_x:52,tile_y:47,
            properties:{gm_hidden:true,dimensions:{width:1,depth:1,height:1}}},
        {name:'Проверочный верхний пол',type:'floor',tile_x:48,tile_y:45,
            properties:{level:1,building_id:'fog-floor',dimensions:{width:3,depth:3,height:.12}}},
    ]},token);
    const hiddenId=objects.objects[0].id;
    const scout={check:{success:true},point:{x:50,y:47,floor_level:0,facing_x:1,facing_y:0}};
    await page.evaluate(`(async()=>{window.T=threeModule;window.L=await import('/static/js/locationScene.js');const add=T.Scene.prototype.add;T.Scene.prototype.add=function(...objects){if(objects.some(o=>o.renderOrder===4||o.userData?.locationObjectId))window.fogCheckScene=this;return add.apply(this,objects)};await enterLocation(${location.id},{scoutView:${JSON.stringify(scout)}});true})()`);
    await page.until('window.fogCheckScene&&document.getElementById("scene-loading-screen").classList.contains("is-hidden")','туман на копии полигона');
    const camera='locationControls.target.set(32,1,28);locationControls.object.position.set(52,40,58);locationControls.update()';
    await page.evaluate(camera);
    const inspect=()=>page.evaluate(`(()=>{const meshes=fogCheckScene.children.filter(m=>m.renderOrder===4&&m.geometry?.index);return meshes.map(m=>({visible:m.visible,capacity:m.geometry.index.array.length,count:m.geometry.drawRange.count}))})()`);
    const before=await inspect();
    assert.equal(before.length,2);
    assert.ok(before.some(mesh=>mesh.visible&&mesh.count>0));
    fs.writeFileSync(path.join(safeSession.artifacts,'fog-visual-polygon-2.png'),
        Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    await request(url,{tiles_data:deep},token,'PUT');
    await page.evaluate('exitLocation();true');
    await page.evaluate(`(async()=>{await enterLocation(${location.id},{scoutView:${JSON.stringify(scout)}});true})()`);
    await page.until('window.fogCheckScene&&document.getElementById("scene-loading-screen").classList.contains("is-hidden")','туман над глубокой водой');
    await page.evaluate(camera);
    const after=await inspect();
    const oldCapacity=64*56*24;
    assert.ok(after.some(mesh=>mesh.capacity>oldCapacity),'крутые берега требуют больше прежнего лимита');
    assert.ok(after.every(mesh=>mesh.count<=mesh.capacity),'индексы тумана не обрезаны');
    fs.writeFileSync(path.join(safeSession.artifacts,'fog-deep-water.png'),
        Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    const contactShadows="document.querySelector('.scene-contact-shadows-button')";
    if (await page.evaluate(`Boolean(${contactShadows})`)) {
        await page.evaluate(`${contactShadows}.click()`);
        await page.until(`${contactShadows}.getAttribute('aria-pressed')==='true'`,'Контактные тени включены над водой');
        await page.evaluate(`(async()=>{await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);})()`);
        assert.deepEqual(await inspect(),after,'Контактные тени сохраняют геометрию и видимость тумана');
        fs.writeFileSync(path.join(safeSession.artifacts,'fog-deep-water-contact.png'),
            Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    }
    await page.evaluate('exitLocation();true');
    await page.evaluate(`(async()=>{await enterLocation(${location.id});true})()`);
    await page.until('window.fogCheckScene&&document.getElementById("scene-loading-screen").classList.contains("is-hidden")','ГМ наблюдает полную сцену');
    const gmHidden=await page.evaluate(`(()=>{const root=fogCheckScene.children.find(mesh=>mesh.userData?.locationObjectId===${hiddenId});return root?.visible??null})()`);
    assert.equal(gmHidden,true,'ГМ видит свой скрытый объект');

    const version=await(await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();
    const protocol=await connect(version.webSocketDebuggerUrl);
    let context,peer;
    try{
        context=await protocol.call('Target.createBrowserContext');
        const target=await protocol.call('Target.createTarget',{url:base,browserContextId:context.browserContextId});
        const tabs=await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json();
        peer=await connect(tabs.find(tab=>tab.id===target.targetId).webSocketDebuggerUrl);
        await peer.until('document.readyState==="complete"','окно игрока');
        await peer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(playerToken)});localStorage.setItem('user_id',${JSON.stringify(String(playerId))});true`);
        await peer.call('Page.navigate',{url:base+api+'/page'});
        await peer.until('window.enterLocation&&window.isGM===false','игрок вошёл в комнату');
        await peer.evaluate(`(async()=>{window.T=await import('three');const add=T.Scene.prototype.add;T.Scene.prototype.add=function(...objects){if(objects.some(o=>o.renderOrder===4))window.fogCheckScene=this;return add.apply(this,objects)};window.L=await import('/static/js/locationScene.js');await enterLocation(${location.id});true})()`);
        await peer.until('window.fogCheckScene&&document.getElementById("scene-loading-screen").classList.contains("is-hidden")','игрок видит туман');
        const playerState=await peer.evaluate(`(()=>{const hidden=fogCheckScene.children.find(mesh=>mesh.userData?.locationObjectId===${hiddenId});const fog=fogCheckScene.children.filter(mesh=>mesh.renderOrder===4&&mesh.visible);return{gm:window.isGM,hidden:hidden?.visible??null,fog:fog.map(mesh=>({count:mesh.geometry.drawRange.count,capacity:mesh.geometry.index.array.length}))}})()`);
        assert.equal(playerState.gm,false);
        assert.equal(playerState.hidden,false,'скрытый объект не раскрыт игроку');
        assert.ok(playerState.fog.length&&playerState.fog.every(mesh=>mesh.count<=mesh.capacity));
        fs.writeFileSync(path.join(safeSession.artifacts,'fog-player-deep-water.png'),
            Buffer.from((await peer.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        await peer.evaluate(`(()=>{window.fogMoves=[];socket.on('character_moved',event=>fogMoves.push(event));socket.emit('move_in_location',{token:localStorage.getItem('access_token'),location_id:${location.id},character_id:${actor.id},x:49,y:47,floor_level:0});return true})()`);
        await peer.until('fogMoves.length>0','движение игрока получено');
        await page.until(`fogCheckScene.children.some(mesh=>mesh.userData?.characterId===${actor.id}&&Math.abs(mesh.position.x-49)<1)`,'ГМ видит перемещение');
        await peer.evaluate('L.setLocationViewLevel(1);true');
        assert.equal(await peer.evaluate('L.getLocationViewLevel()'),1);
        assert.ok((await peer.evaluate('fogCheckScene.children.filter(mesh=>mesh.renderOrder===4&&mesh.visible).length'))>0);
        assert.equal(await peer.evaluate(`fogCheckScene.children.find(mesh=>mesh.userData?.locationObjectId===${hiddenId})?.visible??null`),false);
        await peer.evaluate('L.setLocationViewLevel(0);true');
        const fogBeforeHeight=await peer.evaluate('fogCheckScene.children.find(mesh=>mesh.renderOrder===4&&mesh.visible)?.uuid');
        await page.evaluate('L.levelLocationWaterBody(52,44,"exact",1.5);true');
        await peer.until(`fogCheckScene.children.some(mesh=>mesh.renderOrder===4&&mesh.visible&&mesh.uuid!==${JSON.stringify(fogBeforeHeight)}&&mesh.geometry.drawRange.count<=mesh.geometry.index.array.length)`,'туман обновлён после высоты воды');
        await peer.until(`(async()=>{const r=await fetch(${JSON.stringify(url)},{headers:{Authorization:'Bearer '+localStorage.getItem('access_token')}});const d=await r.json();return d.tiles_data?.[44]?.[52]?.height===1.5})()`,'новая высота воды сохранена');
        await peer.evaluate('window.fogBeforeReload=true;true');
        await peer.call('Page.reload',{ignoreCache:true});
        await peer.until('!window.fogBeforeReload&&typeof enterLocation==="function"&&window.isGM===false','игрок перезагрузил страницу');
        await peer.evaluate(`(async()=>{window.T=await import('three');window.L=await import('/static/js/locationScene.js');const add=T.Scene.prototype.add;T.Scene.prototype.add=function(...objects){if(objects.some(o=>o.renderOrder===4))window.fogCheckScene=this;return add.apply(this,objects)};await enterLocation(${location.id});true})()`);
        await peer.until('window.fogCheckScene&&document.getElementById("scene-loading-screen").classList.contains("is-hidden")','туман восстановлен после перезагрузки');
        assert.equal(await peer.evaluate(`fogCheckScene.children.find(mesh=>mesh.userData?.locationObjectId===${hiddenId})?.visible??null`),false);
        console.log(JSON.stringify({visual_polygon_2:true,deep_water:true,fog_indices:after,gmHidden,playerState,
            movement:true,floorChange:true,heightChange:true,reload:true,twoClients:true}));
    }finally{
        if(context)await protocol.call('Target.disposeBrowserContext',{browserContextId:context.browserContextId});
    }
};
