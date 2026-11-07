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
        name: 'Визуальный полигон 2 — копия проверки болотных луж',
        world_tile_x: 12, world_tile_z: 7, grid_width: 64, grid_height: 56,
        tiles_data: tiles,
    }, token);
    await page.evaluate(`(async()=>{
        const T=threeModule,add=T.Scene.prototype.add;
        T.Scene.prototype.add=function(...objects){
            for(const object of objects)if(object.userData?.detailCounts)window.swampDetailGroup=object;
            return add.apply(this,objects);
        };
        await enterLocation(${location.id});return true;
    })()`);
    await page.until('window.swampDetailGroup&&document.getElementById("scene-loading-screen").classList.contains("is-hidden")',
        'болото на копии полигона');
    await page.evaluate('locationControls.target.set(54,1,45);locationControls.object.position.set(59,14,54);locationControls.update()');
    const result = await page.evaluate(`(async()=>{
        const {swampPlacements}=await import('/static/js/locationGrassDetail.js');
        const pools=swampPlacements(${JSON.stringify(tiles)}).pools;
        return {generated:pools.length,rendered:swampDetailGroup.userData.detailCounts.pools,
            scene:swampDetailGroup.parent?.type,visible:swampDetailGroup.visible};
    })()`);
    assert.ok(result.generated > 10);
    assert.ok(result.rendered > 0 && result.rendered <= result.generated);
    assert.equal(result.scene, 'Scene');
    assert.equal(result.visible, true);
    fs.writeFileSync(path.join(safeSession.artifacts, 'swamp-pools-normal-camera.png'),
        Buffer.from((await page.call('Page.captureScreenshot', { format: 'png' })).data, 'base64'));
    console.log(JSON.stringify({ swampPools: result }));
    await page.evaluate('exitLocation();true');
};
