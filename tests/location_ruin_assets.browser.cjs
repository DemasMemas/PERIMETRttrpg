const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,location,safeSession})=>{
    await page.evaluate(`(()=>{L.setLocationViewLevel(0);window.selectLocationEditorTool('assets');
        [...document.querySelectorAll('.location-asset-tabs button')].find(b=>b.textContent==='Разрушения').click();})()`);
    const entries=await page.evaluate(`(async()=>{const {LOCATION_ASSETS}=await import('/static/js/locationAssetCatalog.js');return LOCATION_ASSETS.filter(a=>a.group==='Разрушения').map(a=>({id:a.id,label:a.label,type:a.baseType}));})()`);
    assert.equal(entries.length,8);assert.equal(await page.evaluate(`document.querySelectorAll('.location-asset-card').length`),8);
    const results=[];let groundIndex=0,holeIndex=0;
    for(const [i,asset]of entries.entries()){
        const hole=asset.type==='floor_opening',index=hole?holeIndex++:groundIndex++;
        const level=Number(hole),x=hole?34:40+index%2*3,z=hole?33+index*3:28+Math.floor(index/2)*3;
        const placed=await page.evaluate(`(async()=>{
            [...document.querySelectorAll('.location-asset-card')].find(b=>b.textContent.includes(${JSON.stringify(asset.label)})).click();
            L.setLocationViewLevel(${level});
            document.querySelector('[data-placement-building-id]').value=${JSON.stringify(level?'pripyat-4':'')};
            document.querySelector('[data-placement-level]').value='${level}';
            L.setLocationStructureRotation(${i===0?Math.PI/6:0});
            return L.placeLibraryAssetForTest(${x},${z});})()`);
        assert.ok(placed.object,asset.id+' размещён');assert.equal(placed.object.type,asset.type);
        assert.equal(placed.object.properties.visual_asset,asset.id);
        const key=asset.type==='household_rubble'?'structural_debris':'structural_damage';
        assert.equal(placed.object.properties[key].version,1);
        assert.deepEqual(placed.preview[key],placed.object.properties[key],asset.id+' предпросмотр соответствует сохранению');
        assert.ok(Math.abs(placed.object.properties.rotation-(i===0?Math.PI/6:0))<1e-10);
        results.push({id:placed.object.id,asset:asset.id,type:asset.type,properties:placed.object.properties});
    }
    await page.evaluate(`(()=>{L.clearLocationAssetPreset();L.setLocationEditMode(false);L.setLocationViewLevel(1);
        const G=L.sceneGraphicsForTest();G.controls.target.set(34,3.4,34.5);G.camera.position.set(39,11,42);G.controls.update();})()`);
    await new Promise(r=>setTimeout(r,400));
    fs.writeFileSync(path.join(safeSession.artifacts,'library-openings.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    await page.evaluate(`(()=>{L.setLocationViewLevel(0);
        const G=L.sceneGraphicsForTest();G.controls.target.set(41.5,1,31);G.camera.position.set(48,11,43);G.controls.update();})()`);
    await new Promise(r=>setTimeout(r,400));
    fs.writeFileSync(path.join(safeSession.artifacts,'library-ruins.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    const saved=await request('/lobbies/'+lobby.id+'/locations/'+location.id,null,token,'GET');
    for(const placed of results){
        const object=saved.objects.find(o=>o.id===placed.id);assert.ok(object);assert.deepEqual(object.properties,placed.properties);
    }
    await page.evaluate(`window.exitLocation()`);
    await page.evaluate(`enterLocation(${location.id})`);
    await page.until(`window.locationControls&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Повторная загрузка библиотеки и сохранённых объектов');
    const restored=await page.evaluate(`(()=>{const G=L.sceneGraphicsForTest();return G.roots.filter(r=>${JSON.stringify(results.map(o=>o.id))}.includes(r.userData.locationObjectId)).map(r=>({id:r.userData.locationObjectId,asset:r.userData.locationObject.properties.visual_asset,meshes:r.children.length}));})()`);
    assert.equal(restored.length,8);assert.ok(restored.every(o=>o.meshes>0));
    fs.writeFileSync(path.join(safeSession.artifacts,'library-assets.json'),JSON.stringify({results,restored},null,2));
    console.log('Библиотека разрушений: 8 вариантов, предпросмотр, поворот, сохранение и повторная загрузка проверены.');
};
