const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

module.exports = async ({ page, request, token, lobby, safeSession }) => {
    const root = path.join(__dirname, '..');
    const python = path.join(root, '.venv', 'Scripts', 'python.exe');
    const code = `import sys,json;sys.path.insert(0,${JSON.stringify(root)});from scripts.seed_visual_test_location import _stress_tiles;print(json.dumps(_stress_tiles()))`;
    const generated = spawnSync(python, ['-I', '-B', '-c', code],
        { cwd: root, encoding: 'utf8', maxBuffer: 8 * 1024 * 1024 });
    assert.equal(generated.status, 0, generated.stderr);
    const tiles = JSON.parse(generated.stdout);
    const location = await request(`/lobbies/${lobby.id}/locations`, {
        name: 'Визуальный полигон 2 — копия проверки травы',
        world_tile_x: 12, world_tile_z: 7, grid_width: 64, grid_height: 56,
        tiles_data: tiles,
    }, token);
    await page.evaluate(`(async()=>{
        const T=threeModule,add=T.Scene.prototype.add;
        T.Scene.prototype.add=function(...objects){
            for(const object of objects)if(object.userData?.detailCounts)window.grassVariationGroup=object;
            return add.apply(this,objects);
        };
        window.grassModule=await import('/static/js/locationScene.js');
        await enterLocation(${location.id});return true;
    })()`);
    await page.until(`getLocationGrassDiagnostics().assets==='loaded'
        &&getLocationGrassDiagnostics().kinds.medium01?.added>0
        &&getLocationGrassDiagnostics().kinds.bermuda?.added>0`, 'оба вида травы загружены');
    await page.until('document.getElementById("scene-loading-screen").classList.contains("is-hidden")',
        'сцена травы показана');
    await page.until('getComputedStyle(document.getElementById("scene-loading-screen")).visibility==="hidden"',
        'экран загрузки исчез');
    await page.evaluate('locationControls.target.set(19,1,39);locationControls.object.position.set(22,3,45);locationControls.update()');
    const grass = await page.evaluate('getLocationGrassDiagnostics()');
    assert.equal(grass.kinds.medium01.material.alphaMapLoaded, true);
    assert.equal(grass.kinds.bermuda.material.alphaMapLoaded, true);
    assert.equal(grass.kinds.bermuda.material.normalMapLoaded, true);
    assert.ok(Math.abs(grass.kinds.medium01.firstInstance.bottomAboveGround) < 0.04);
    assert.ok(Math.abs(grass.kinds.bermuda.firstInstance.bottomAboveGround) < 0.04);
    const total = grass.kinds.medium01.added + grass.kinds.bermuda.added;
    assert.ok(grass.kinds.bermuda.added > total * 0.1);
    assert.ok(grass.triangles.medium01 + grass.triangles.bermuda <= total * 457);
    fs.writeFileSync(path.join(safeSession.artifacts, 'grass-variations-normal-camera.png'),
        Buffer.from((await page.call('Page.captureScreenshot', { format: 'png' })).data, 'base64'));
    const ids = await page.evaluate(`(()=>Object.fromEntries(grassVariationGroup.children
        .filter(mesh=>mesh.userData.naturalGrassKind)
        .map(mesh=>[mesh.userData.naturalGrassKind+':'+mesh.userData.naturalGrassChunk,mesh.uuid])))()`);
    assert.ok(ids['medium01:1,3'] && ids['bermuda:1,3']);
    await page.evaluate('grassModule.applyLocationBrush(19,39,{grass_subtype:"forest"},0);true');
    await page.until(`(()=>{const meshes=Object.fromEntries(grassVariationGroup.children
        .filter(mesh=>mesh.userData.naturalGrassKind)
        .map(mesh=>[mesh.userData.naturalGrassKind+':'+mesh.userData.naturalGrassChunk,mesh.uuid]));
        return meshes['medium01:1,3']!==${JSON.stringify(ids['medium01:1,3'])}
            &&meshes['bermuda:1,3']!==${JSON.stringify(ids['bermuda:1,3'])}
            &&meshes['medium01:0,0']===${JSON.stringify(ids['medium01:0,0'])};})()`,
    'оба вида травы обновлены в изменённом фрагменте');
    console.log(JSON.stringify({ grassVariations: { kinds: {
        medium01: grass.kinds.medium01.added, bermuda: grass.kinds.bermuda.added },
    triangles: grass.triangles, chunks: {
        medium01: grass.kinds.medium01.chunks, bermuda: grass.kinds.bermuda.chunks } } }));
    await page.evaluate('exitLocation();true');
};
