// Used by the existing isolated Flask/Chrome game harness with VTT_TERRAIN_ONLY=1.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
module.exports = async ({ page, request, token, lobby, profile, base, connect, debugPort }) => {
    const kinds = ['grass', 'sand', 'rock', 'swamp', 'concrete', 'gravel', 'snow', 'metal',
        'bare_dirt', 'granite_tiles', 'wood_floor'];
    const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
    const capture = async name => {
        await page.evaluate(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`);
        const file = path.join(profile, `${name}.png`);
        fs.writeFileSync(file, Buffer.from((await page.call('Page.captureScreenshot', { format: 'png' })).data, 'base64'));
        console.log('Scene screenshot:', file);
    };
    // Legacy world floor tiles below remain renderable, but cannot be newly selected.
    for (const id of ['tile-type-select','tile-edit-terrain']) for (const kind of ['wood_floor','granite_tiles']) {
        assert.equal(await page.evaluate(`document.querySelector('#${id} option[value=${kind}]') !== null`), false);
    }
    const updates = [];
    for (let z = 6; z < 26; z++) for (let x = 20; x < 20 + kinds.length * 3; x++) {
        const terrain = x >= 21 && x <= 23 && z >= 21 && z <= 23 ? 'water' : kinds[Math.floor((x - 20) / 3)];
        updates.push({ chunk_x: x >> 5, chunk_y: 0, tile_x: x % 32, tile_y: z,
            updates: { terrain, height: terrain === 'rock' && z > 16 ? 3 : 1, objects: [] } });
    }
    const reviewKinds = ['sand','snow','swamp','wood_floor'];
    for (let z=6;z<26;z++) for(let x=0;x<20;x++) {
        updates.push({chunk_x:x>>5,chunk_y:z>>5,tile_x:x%32,tile_y:z%32,
            updates:{terrain:reviewKinds[Math.floor(x/10)+2*Math.floor((z-6)/10)],height:1,objects:[]}});
    }
    await request(`/lobbies/${lobby.id}/chunks/batch`, updates, token);
    await page.until(`sceneModule.chunksMap.get('1,0').tilesData[10][0].terrain === 'concrete'`, 'world socket update');
    await page.until(`sceneModule.getWorldTerrainMaterialDiagnostics().ready`, 'shared terrain images');
    await page.evaluate(`sceneModule.controls.target.set(36.5,1,16);sceneModule.camera.position.set(47.5,20,38);sceneModule.controls.update()`);
    await pause(1500);
    console.log('World terrain:', await page.evaluate(`JSON.stringify({materials:sceneModule.getWorldTerrainMaterialDiagnostics(),calls:sceneModule.renderer.info.render.calls,
        triangles:sceneModule.renderer.info.render.triangles,errors:sceneModule.renderer.info.programs.filter(p=>p.diagnostics?.runnable===false).length})`));
    assert.equal(await page.evaluate(`sceneModule.renderer.info.programs.filter(p=>p.diagnostics?.runnable===false).length`), 0);
    await capture('terrain-world');
    await page.evaluate(`sceneModule.setCelestialTime(960);sceneModule.camera.position.set(23,17,35);sceneModule.controls.update()`);
    await pause(600);
    await capture('terrain-world-afternoon');
    await page.evaluate(`sceneModule.controls.target.set(10,1,16);sceneModule.camera.position.set(16,25,40);sceneModule.controls.update()`);
    await capture('terrain-world-large-patches');
    if(process.env.VTT_MATERIAL_DETAIL) {
        for(const [kind,x,z] of [['sand',5,11],['snow',15,11],['swamp',5,21]]) {
            await page.evaluate(`sceneModule.controls.target.set(${x},1,${z});sceneModule.camera.position.set(${x+2},5,${z+4});sceneModule.controls.update()`);
            await capture(`detail-world-${kind}`);
        }
    }
    await page.evaluate(`sceneModule.setCelestialTime(480);sceneModule.camera.position.set(47.5,20,38);sceneModule.controls.update()`);
    // Compare the same meshes/camera with the added PBR branch on/off. This is
    // a software-renderer diagnostic, not a hardware FPS benchmark.
    console.log('World PBR comparison:', await page.evaluate(`(async()=>{
        let ready;
        sceneModule.scene.traverse(mesh=>{for(const material of [].concat(mesh.material||[])) {
            const uniform=sceneModule.renderer.properties.get(material).uniforms?.terrainAtlasReady;
            if(uniform) ready=uniform;
        }});
        if(!ready) return null;
        const sample=async value=>{ready.value=value;await new Promise(requestAnimationFrame);
            const times=[];for(let i=0;i<3;i++)times.push(await new Promise(requestAnimationFrame));
            return {frameIntervalMs:(times[2]-times[0])/2,calls:sceneModule.renderer.info.render.calls,
                triangles:sceneModule.renderer.info.render.triangles};};
        const pbr=await sample(1), base=await sample(0);ready.value=1;return {pbr,base};
    })()`));
    const persisted = await request(`/lobbies/${lobby.id}/chunks?min_chunk_x=1&max_chunk_x=1&min_chunk_y=0&max_chunk_y=0`, null, token, 'GET');
    assert.equal(persisted[0].data[10][0].terrain, 'concrete');
    for (const kind of ['bare_dirt', 'granite_tiles', 'wood_floor']) {
        assert.ok(persisted[0].data.some(row => row.some(tile => tile.terrain === kind)));
    }
    console.log('WebGL1:', await page.evaluate(`(async()=>{
        const THREE=await import('three');const {createTerrainMaterialLayer}=await import('/static/js/terrainMaterialLayer.js');
        const canvas=document.createElement('canvas');const context=canvas.getContext('webgl');
        if(!context)return {available:false};
        const renderer=new THREE.WebGLRenderer({canvas,context});renderer.setSize(64,64);
        const layer=createTerrainMaterialLayer(THREE);await layer.ready;layer.resize(2,2);layer.fill([[{terrain:'rock'},{terrain:'sand'}],[{terrain:'concrete'},{terrain:'metal'}]]);
        const material=new THREE.MeshStandardMaterial();layer.apply(material);
        const geometry=new THREE.PlaneGeometry(2,2);geometry.rotateX(-Math.PI/2);
        const scene=new THREE.Scene();scene.add(new THREE.Mesh(geometry,material),new THREE.AmbientLight(0xffffff,1));
        const camera=new THREE.PerspectiveCamera(45,1,0.1,100);camera.position.set(2,4,4);camera.lookAt(0,0,0);
        renderer.render(scene,camera);const errors=renderer.info.programs.filter(p=>p.diagnostics?.runnable===false).length;
        // A GPU readback verifies actual atlas pixels, not just shader text.
        layer.fill([[{terrain:'snow'},{terrain:'snow'}],[{terrain:'snow'},{terrain:'snow'}]]);
        const originalCompile=material.onBeforeCompile;
        material.onBeforeCompile=shader=>{
            originalCompile(shader);
            if(!shader.fragmentShader.includes('#include <tonemapping_fragment>')) throw new Error('Missing probe output hook');
            shader.fragmentShader=shader.fragmentShader.replace('#include <tonemapping_fragment>',
                'gl_FragColor = vec4(terrainColor, 1.0);' + String.fromCharCode(10) + '#include <tonemapping_fragment>');
        };
        material.customProgramCacheKey=()=> 'terrain-snow-readback';material.needsUpdate=true;
        const target=new THREE.WebGLRenderTarget(64,64);renderer.setRenderTarget(target);
        renderer.setClearColor(0x000000,0);renderer.render(scene,camera);
        const pixels=new Uint8Array(64*64*4);renderer.readRenderTargetPixels(target,0,0,64,64,pixels);
        let min=1,max=0,count=0;
        for(let i=0;i<pixels.length;i+=4) if(pixels[i+3]>250) {
            const gray=(pixels[i]+pixels[i+1]+pixels[i+2])/765;
            min=Math.min(min,gray);max=Math.max(max,gray);count++;
        }
        if(count<100 || min<0.6 || max-min<0.002) throw new Error('Snow GPU range invalid: '+JSON.stringify({min,max,count}));
        console.log('Snow GPU pixels:',JSON.stringify({min,max,count}));
        renderer.setRenderTarget(null);target.dispose();
        geometry.dispose();material.dispose();layer.dispose();renderer.dispose();renderer.forceContextLoss();
        return {available:true,errors,snowPixels:{min,max,count}};
    })()`));
    const target = await (await fetch(`http://127.0.0.1:${debugPort}/json/new?${encodeURIComponent(`${base}/lobbies/${lobby.id}/page`)}`, { method: 'PUT' })).json();
    const peer = await connect(target.webSocketDebuggerUrl);
    try {
    await peer.call('Runtime.enable');
    await peer.call('Page.bringToFront');
    await peer.until(`window.socket && window.isGM && document.getElementById('scene-loading-screen').classList.contains('is-hidden')`, 'second game client');
    await peer.evaluate(`(async()=>{window.terrainPeerWorld=await import('/static/js/lobby3d.js')})()`);
    await peer.until(`terrainPeerWorld.chunksMap.get('1,0')?.tilesData[10][19].terrain==='wood_floor'`, 'second world client loads wooden floor');
    await request(`/lobbies/${lobby.id}/chunks/1/0/tile/0/11`, { terrain: 'gravel' }, token, 'PATCH');
    await peer.until(`terrainPeerWorld.chunksMap.get('1,0')?.tilesData[11][0].terrain==='gravel'`, 'second world client applies material');
    for (const terrain of reviewKinds) {
        await request(`/lobbies/${lobby.id}/chunks/1/0/tile/1/11`, {terrain}, token, 'PATCH');
        await peer.until(`terrainPeerWorld.chunksMap.get('1,0')?.tilesData[11][1].terrain==='${terrain}'`, `second world ${terrain}`);
        const saved = await request(`/lobbies/${lobby.id}/chunks?min_chunk_x=1&max_chunk_x=1&min_chunk_y=0&max_chunk_y=0`, null, token, 'GET');
        assert.equal(saved[0].data[11][1].terrain, terrain);
    }
    const sizes = process.env.VTT_TERRAIN_SIZES ? process.env.VTT_TERRAIN_SIZES.split(',').map(Number) : [20,40,80];
    for (const size of sizes) {
        const tiles = Array.from({ length: size }, (_, z) => Array.from({ length: size }, (_, x) => {
            const terrain = x >= size-4 && x <= size-2 && z >= 2 && z <= 4
                ? 'water' : kinds[Math.min(kinds.length - 1, Math.floor(x * kinds.length / size))];
            return { terrain, height: terrain === 'rock' && z > size / 2 ? 3
                : terrain === 'grass' ? 1 + Math.max(0, z - size * 0.4) * 0.04 : 1,
                ...(terrain === 'grass' && z > size / 2 ? { grass_subtype: 'forest' } : {}), objects: [] };
        }));
        if(size===40) for(let z=22;z<38;z++) for(let x=2;x<38;x++) {
            tiles[z][x]={terrain:reviewKinds[Math.floor((x-2)/9)],height:1,objects:[]};
        }
        const location = await request(`/lobbies/${lobby.id}/locations`, { name: `Terrain ${size}`,
            world_tile_x: 25, world_tile_z: 15, grid_width: size, grid_height: size, tiles_data: tiles }, token);
        if (size === 20) {
            await peer.call('Page.bringToFront');
            await peer.evaluate(`window.enterLocation(${location.id})`);
            await peer.until(`document.getElementById('scene-loading-screen').classList.contains('is-hidden')`, 'second local scene first frame');
            await peer.evaluate(`window.terrainPeerUpdates=[];window.socket.on('location_tiles_updated',data=>window.terrainPeerUpdates.push(data));true`);
        }
        await page.call('Page.bringToFront');
        await page.evaluate(`window.enterLocation(${location.id})`);
        await page.until(`window.currentLocationId === ${location.id} && document.getElementById('scene-loading-screen').classList.contains('is-hidden')`, `local ${size} scene`);
        await page.evaluate(`(async()=>{window.terrainLocation = await import('/static/js/locationScene.js')})()`);
        await page.until(`terrainLocation.getLocationTerrainMaterialDiagnostics()?.ready`, 'local terrain ready');
        await page.evaluate(`document.querySelector('#panel-tools:not(.collapsed) .toggle-btn')?.click()`);
        await pause(1200);
        console.log(`Local ${size}:`, await page.evaluate(`JSON.stringify({materials:terrainLocation.getLocationTerrainMaterialDiagnostics(),load:window.getLocationLoadDiagnostics?.()})`));
        assert.equal(await page.evaluate(`document.querySelector('#loc-edit-terrain option[value=metal]') !== null`), true);
        assert.equal(await page.evaluate('terrainLocation.getLocationTerrainMaterialDiagnostics().shaderErrors'), 0);
        if (size < 80) await capture(`terrain-local-${size}`);
        if(size===40) {
            await page.evaluate(`window.locationControls.target.set(20,1,30);window.locationControls.object.position.set(24,24,55);window.locationControls.update()`);
            await capture('terrain-local-large-patches');
            if(process.env.VTT_MATERIAL_DETAIL) {
                for(const [kind,x,z] of [['sand',6,30],['snow',15,30],['swamp',24,30]]) {
                    await page.evaluate(`window.locationControls.target.set(${x},1,${z});window.locationControls.object.position.set(${x+2},5,${z+4});window.locationControls.update()`);
                    await capture(`detail-local-${kind}`);
                }
            }
        }
        const saved = await request(`/lobbies/${lobby.id}/locations/${location.id}`, null, token, 'GET');
        assert.deepEqual(saved.tiles_data, tiles);
        for (const kind of ['bare_dirt','granite_tiles','wood_floor']) {
            assert.ok(saved.tiles_data.some(row=>row.some(tile=>tile.terrain===kind)));
            assert.equal(await page.evaluate(`document.querySelector('#loc-edit-terrain option[value=${kind}]') !== null`), true);
        }
        if(size===20) for(const terrain of reviewKinds) {
            await page.evaluate(`terrainLocation.setLocationEditMode(true);terrainLocation.applyLocationBrush(4,3,{terrain:'${terrain}'},0)`);
            await peer.until(`window.terrainPeerUpdates.some(data=>data.location_id===${location.id} && data.updates.some(tile=>tile.x===4 && tile.z===3 && tile.terrain==='${terrain}'))`, `second local ${terrain}`);
            const saved = await request(`/lobbies/${lobby.id}/locations/${location.id}`,null,token,'GET');
            assert.equal(saved.tiles_data[3][4].terrain,terrain);
        }
        // Exercise the real local brush and save/socket path without generating new data formats.
        await page.evaluate(`terrainLocation.setLocationEditMode(true);terrainLocation.applyLocationBrush(2,2,{terrain:'wood_floor'},0)`);
        await pause(600);
        let edited;
        for(let attempt=0;attempt<80;attempt++) {
            edited = await request(`/lobbies/${lobby.id}/locations/${location.id}`, null, token, 'GET');
            if(edited.tiles_data[2][2].terrain==='wood_floor') break;
            await pause(250);
        }
        assert.equal(edited.tiles_data[2][2].terrain, 'wood_floor');
        if (size === 20) {
            await peer.until(`window.terrainPeerUpdates.some(data=>data.location_id===${location.id} && data.updates.some(tile=>tile.x===2 && tile.z===2 && tile.terrain==='wood_floor'))`, 'second local client receives brush');
            await page.evaluate(`window.enterLocation(${location.id})`);
            await page.until(`document.getElementById('scene-loading-screen').classList.contains('is-hidden')`, 'reload saved location');
            await page.evaluate(`terrainLocation.applyLocationLighting({mode:'manual',ambient_color:'#ffffff',ambient_intensity:0.65,
                directional_color:'#ffffff',directional_intensity:1.1,directional_azimuth:130,directional_elevation:60},false)`);
            await page.evaluate(`document.querySelector('#panel-tools:not(.collapsed) .toggle-btn')?.click()`);
            await pause(600);
            await capture('terrain-local-20-neutral');
            await page.until(`window.getLocationGrassDiagnostics?.().assets==='loaded' && window.getLocationGrassDiagnostics?.().kinds?.low?.added>0`, 'natural grass roots');
            console.log('Grass roots:',await page.evaluate(`JSON.stringify(window.getLocationGrassDiagnostics())`));
            const clearance = await page.evaluate(`window.getLocationGrassDiagnostics().kinds.low.firstInstance.bottomAboveGround`);
            assert.ok(clearance < -0.014 && clearance > -0.022, `grass root ${clearance}`);
            await page.evaluate(`window.locationControls.target.set(1,1.3,10);
                window.locationControls.object.position.set(5,4,15);window.locationControls.update()`);
            await pause(600);
            await capture('grass-local-slope');
            await page.evaluate(`terrainLocation.applyLocationBrush(1,10,{height:1.6},0)`);
            await pause(600);
            await capture('grass-local-edited-height');
            // Remaining sizes need only one visible renderer.
            await peer.call('Page.navigate', {url:'about:blank'});
        }
    }
    console.log('PASS: world/local PBR game scenes, world cross-chunk socket updates, second world/local client, local brush persistence and reload, shared atlases');
    } finally { peer.close(); }
};
