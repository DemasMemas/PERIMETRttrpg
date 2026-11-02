// Проверка нового грунта на копии Припяти и глобальной карте в изолированной БД.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,safeSession,request,token,lobby,location,actor})=>{
    const api=`/lobbies/${lobby.id}`,url=api+'/locations/'+location.id;
    const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
    const capture=async name=>{
        await pause(350);
        fs.writeFileSync(path.join(safeSession.artifacts,name+'.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    };
    const saved=async()=>request(url,null,token,'GET');
    const waitSaved=async check=>{
        for(let i=0;i<60;i++){const data=await saved();if(check(data))return data;await pause(100);}
        throw Error('Изменение грунта не сохранилось');
    };
    await page.until('L.getLocationTerrainMaterialDiagnostics()?.ready','материалы Припяти');
    const original=await saved();
    assert.equal(await page.evaluate(`document.querySelector('#loc-edit-terrain option[value=ice]')?.textContent`),'Лёд');
    await page.evaluate(`(()=>{const select=document.getElementById('loc-edit-terrain');select.value='ice';select.dispatchEvent(new Event('change',{bubbles:true}));L.setLocationEditMode(true);L.applyLocationBrush(43,48,{terrain:select.value},2);})()`);
    const edited=await waitSaved(data=>data.tiles_data[48][43].terrain==='ice');
    assert.equal(edited.tiles_data[46][41].terrain,'ice');
    assert.equal(edited.tiles_data[50][45].terrain,'ice');
    assert.equal(edited.tiles_data[48][43].height,original.tiles_data[48][43].height,'кисть покрытия сохраняет высоту');
    const route=await request(url+'/movement-preview',{character_id:actor.id,x:44,y:48,floor_level:0},token);
    assert.ok(route.route,'лёд является проходимым грунтом');
    await page.evaluate(`Boolean(window.G=L.sceneGraphicsForTest());L.setLocationEditMode(false);document.querySelector('#panel-tools:not(.collapsed) .toggle-btn')?.click()`);
    for(const [name,offset] of [['ice-local-overview',[10,14,13]],['ice-local-close',[2,4,5]],['ice-local-angle',[-5,3,3]]]){
        await page.evaluate(`(()=>{G.controls.target.set(43,1,48);G.camera.position.copy(G.controls.target).add(new T.Vector3(...${JSON.stringify(offset)}));G.controls.update();})()`);
        await capture(name);
    }
    const localDiagnostics=await page.evaluate('L.getLocationTerrainMaterialDiagnostics()');
    assert.equal(localDiagnostics.shaderErrors,0);
    // Повторное открытие читает сохранённый грунт, а не прежние данные сцены.
    await page.evaluate(`exitLocation();enterLocation(${location.id})`);
    await page.until(`window.currentLocationId===${location.id}&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')&&L.getLocationTerrainMaterialDiagnostics()?.ready`,'повторное открытие Припяти');
    assert.equal((await saved()).tiles_data[48][43].terrain,'ice');
    await page.evaluate('exitLocation()');
    await page.until(`document.getElementById('location-container').style.display!=='block'&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')&&sceneModule.chunksMap.has('0,0')`,'глобальная карта');
    for(const id of ['tile-type-select','tile-edit-terrain'])assert.equal(await page.evaluate(`document.querySelector('#${id} option[value=ice]')?.textContent`),'Лёд');
    // Снег вокруг льда воспроизводит присланное автором сочетание покрытий.
    const snowUpdates=[];
    for(let z=8;z<=16;z++)for(let x=8;x<=16;x++)snowUpdates.push({chunk_x:0,chunk_y:0,tile_x:x,tile_y:z,updates:{terrain:'snow',height:1}});
    await request(api+'/chunks/batch',snowUpdates,token);
    await page.until(`sceneModule.chunksMap.get('0,0').tilesData[8][8].terrain==='snow'`,'снежное окружение льда');
    await page.evaluate(`(async()=>{const edit=await import('/static/js/mapEdit.js');edit.setEditMode(true);const select=document.getElementById('tile-type-select');select.value='ice';select.dispatchEvent(new Event('change',{bubbles:true}));edit.applyBrush({chunkX:0,chunkY:0,tileX:12,tileY:12},{terrain:select.value},2);})()`);
    let chunks;
    for(let i=0;i<60;i++){
        chunks=await request(api+'/chunks?min_chunk_x=0&max_chunk_x=0&min_chunk_y=0&max_chunk_y=0',null,token,'GET');
        if(chunks[0].data[12][12].terrain==='ice')break;
        await pause(100);
    }
    assert.equal(chunks[0].data[12][12].terrain,'ice');
    await page.until(`sceneModule.chunksMap.get('0,0').tilesData[12][12].terrain==='ice'&&sceneModule.getWorldTerrainMaterialDiagnostics()?.ready`,'лёд глобальной карты');
    await page.evaluate(`sceneModule.controls.target.set(12,1,12);sceneModule.camera.position.set(19,12,23);sceneModule.controls.update()`);
    await page.until(`sceneModule.chunksMap.get('0,0').ground?.visible&&sceneModule.renderer.info.render.calls>1`,'грунт глобальной карты видим');
    await capture('ice-world');
    for(const [name,position] of [['ice-world-angle',[8,7,18]],['ice-world-top',[12,7,12.01]],['ice-world-overview',[27,31,46]]]){
        await page.evaluate(`sceneModule.camera.position.set(...${JSON.stringify(position)});sceneModule.controls.update()`);
        await capture(name);
    }
    await page.evaluate(`sceneModule.setCelestialTime(720);sceneModule.camera.position.set(12,7,12.01);sceneModule.controls.update()`);
    await capture('ice-snow-noon');
    const transparency=await page.evaluate(`(()=>{
        const e=sceneModule.chunksMap.get('0,0'), r=sceneModule.renderer;
        const glaze=e.iceSurface, original=e.terrainSurface.material;
        if(!glaze)throw Error('Нет прозрачного слоя льда');
        const target=new T.WebGLRenderTarget(64,64), previous=r.getRenderTarget(), pixels=new Uint8Array(64*64*4);
        const red=new T.MeshBasicMaterial({color:0x993333}),blue=new T.MeshBasicMaterial({color:0x333399});
        const sample=(material,visible)=>{
            e.terrainSurface.material=material;glaze.visible=visible;
            r.setRenderTarget(target);r.render(sceneModule.scene,sceneModule.camera);r.readRenderTargetPixels(target,0,0,64,64,pixels);
            const rgb=[0,0,0];for(let y=29;y<35;y++)for(let x=29;x<35;x++)for(let c=0;c<3;c++)rgb[c]+=pixels[(y*64+x)*4+c]/36;
            return rgb;
        };
        try{
            const a=sample(red,true),b=sample(blue,true),c=sample(red,false),d=sample(blue,false);
            const distance=(x,y)=>x.reduce((sum,v,i)=>sum+Math.abs(v-y[i]),0);
            return {transparent:glaze.material.transparent,depthWrite:glaze.material.depthWrite,
                groundOpaque:!original.transparent,throughRatio:distance(a,b)/distance(c,d),surfaceContribution:distance(a,c)};
        }finally{e.terrainSurface.material=original;glaze.visible=true;r.setRenderTarget(previous);target.dispose();red.dispose();blue.dispose();}
    })()`);
    assert.equal(transparency.transparent,true);
    assert.equal(transparency.depthWrite,false);
    assert.equal(transparency.groundOpaque,true);
    assert.ok(transparency.throughRatio>0.2&&transparency.throughRatio<0.95,'цвет основания просвечивает через верхний слой');
    assert.ok(transparency.surfaceContribution>2,'верхний слой действительно участвует в отрисовке');
    const worldDiagnostics=await page.evaluate('sceneModule.getWorldTerrainMaterialDiagnostics()');
    assert.equal(await page.evaluate('sceneModule.renderer.info.programs.filter(p=>p.diagnostics?.runnable===false).length'),0);
    const borderIce=[{chunk_x:0,chunk_y:0,tile_x:31,tile_y:12,updates:{terrain:'ice',height:1.6}},
        {chunk_x:1,chunk_y:0,tile_x:0,tile_y:12,updates:{terrain:'ice',height:1.3}}];
    await request(api+'/chunks/batch',borderIce,token);
    await page.until(`['0,0','1,0'].every(key=>sceneModule.chunksMap.get(key)?.iceSurface)&&
        sceneModule.chunksMap.get('0,0').iceSurface.geometry.attributes.position.array.some((v,i)=>i%3===1&&v>1.5)`,'склон льда на стыке чанков');
    const slopes=await page.evaluate(`(()=>{
        const entries=['0,0','1,0'].map(key=>sceneModule.chunksMap.get(key));
        return {sharedMaterial:entries[0].iceSurface.material===entries[1].iceSurface.material,
            followsGround:entries.every(e=>{
                const base=e.terrainSurface.geometry.attributes.position,top=e.iceSurface.geometry.attributes.position;
                const points=new Set();for(let i=0;i<base.count;i++)points.add([base.getX(i),base.getY(i)+.002,base.getZ(i)].map(v=>v.toFixed(4)).join(','));
                for(let i=0;i<top.count;i++)if(!points.has([top.getX(i),top.getY(i),top.getZ(i)].map(v=>v.toFixed(4)).join(',')))return false;
                return true;
            })};
    })()`);
    assert.equal(slopes.sharedMaterial,true);
    assert.equal(slopes.followsGround,true,'прозрачная поверхность повторяет геометрию склона');
    await request(api+'/chunks/batch',borderIce.map(cell=>({...cell,updates:{terrain:'grass',height:1}})),token);
    await page.until(`!sceneModule.chunksMap.get('1,0').iceSurface&&
        !sceneModule.chunksMap.get('0,0').iceSurface.geometry.attributes.position.array.some((v,i)=>i%3===1&&v>1.01)`,'восстановлен ровный грунт');
    await page.evaluate(`window.iceGeometryDisposed=false;sceneModule.chunksMap.get('0,0').iceSurface.geometry.addEventListener('dispose',()=>window.iceGeometryDisposed=true)`);
    const iceCells=[];
    for(let z=10;z<=14;z++)for(let x=10;x<=14;x++)iceCells.push({chunk_x:0,chunk_y:0,tile_x:x,tile_y:z,updates:{terrain:'snow'}});
    await request(api+'/chunks/batch',iceCells,token);
    await page.until(`!sceneModule.chunksMap.get('0,0').iceSurface&&window.iceGeometryDisposed`,'слой удалён и геометрия освобождена после стирания льда');
    await request(api+'/chunks/batch',iceCells.map(cell=>({...cell,updates:{terrain:'ice'}})),token);
    await page.until(`Boolean(sceneModule.chunksMap.get('0,0').iceSurface)`,'лёд восстановлен');
    await page.call('Page.reload',{ignoreCache:true});
    await page.until('Boolean(window.toggleEditMode&&window.isGM)','комната после перезагрузки');
    await page.evaluate(`(async()=>{window.sceneModule=await import('/static/js/lobby3d.js')})()`);
    await page.until(`sceneModule.chunksMap.get('0,0')?.tilesData[12][12].terrain==='ice'&&sceneModule.getWorldTerrainMaterialDiagnostics()?.ready`,'лёд после перезагрузки браузера');
    const result={scene:'Копия Припяти',localSaved:true,localReopened:true,worldSaved:true,worldReloaded:true,walkable:true,localDiagnostics,worldDiagnostics,transparency,slopes,iceLayerCleanup:true};
    fs.writeFileSync(path.join(safeSession.artifacts,'ice.json'),JSON.stringify(result,null,2));
    console.log('Лёд: оба редактора, сохранение, повторное открытие, проходимость и GPU-отрисовка — проверено.');
};
