// Isolated diagnostic captures. Never changes production files, atlas maps or UI.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
module.exports = async ({ page, request, token, lobby }) => {
    const output=path.resolve(__dirname,'../docs/material-previews/swamp-diagnostics');
    fs.mkdirSync(output,{recursive:true});
    const files=['terrainMaterialCatalog.js','terrainMaterialLayer.js','locationTerrainTexture.js','lobby3d.js','locationScene.js'];
    const hashes=()=>Object.fromEntries(files.map(file=>[file,crypto.createHash('sha256').update(fs.readFileSync(path.resolve(__dirname,'../app/static/js',file))).digest('hex')]));
    const before=hashes();
    const updates=[];
    for(let z=4;z<28;z++)for(let x=4;x<44;x++)updates.push({chunk_x:x>>5,chunk_y:0,tile_x:x%32,tile_y:z,
        updates:{terrain:x<24?'bare_dirt':'swamp',height:1,objects:[]}});
    await request(`/lobbies/${lobby.id}/chunks/batch`,updates,token);
    await page.until("sceneModule.chunksMap.get('1,0').tilesData[16][2].terrain==='swamp' && sceneModule.getWorldTerrainMaterialDiagnostics().ready",'diagnostic terrain');
    await page.evaluate('sceneModule.controls.target.set(34,1,16);sceneModule.camera.position.set(40.25,21,41);sceneModule.controls.update()');
    await page.evaluate('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');
    await page.until(`(()=>{let ready=false;sceneModule.scene.traverse(n=>{for(const m of [].concat(n.material||[]))if(sceneModule.renderer.properties.get(m).uniforms?.terrainAtlasColor)ready=true;});return ready})()`,'compiled terrain shader');
    // Preserve, rather than edit, the rejected B2 expression. Reproduce its mask
    // on the CURRENT atlas to separate that expression from candidate asset choice.
    const rejected=fs.readFileSync(path.join(__dirname,'terrain_material_candidates.browser.cjs'),'utf8');
    const start=rejected.indexOf('const bog = wet =>'),end=rejected.indexOf('    for(const [name,wet]',start);
    assert.ok(start>=0&&end>start);
    const b2=vm.runInNewContext(rejected.slice(start,end)+';bog(0.48)');
    await page.evaluate(`(()=>{
        let mode='current', replay='';const materials=[];
        sceneModule.scene.traverse(n=>{for(const material of [].concat(n.material||[])){
            if(!material.isMeshStandardMaterial || materials.includes(material))continue;
            const uniforms=sceneModule.renderer.properties.get(material).uniforms;
            if(!uniforms?.terrainAtlasColor)continue;
            materials.push(material);
            const compile=material.onBeforeCompile,key=material.customProgramCacheKey.bind(material);
            material.customProgramCacheKey=()=>key()+':swamp-diagnostic:'+mode;
            material.onBeforeCompile=shader=>{
                compile(shader);let s=shader.fragmentShader;
                if(!s.includes('void terrainReadMaterial'))throw new Error('Terrain hook missing');
                if(['no-legacy','base','matte','color-only'].includes(mode)){
                    const cpu='diffuseColor.rgb *= worldGroundTexel.rgb;';
                    if(s.includes(cpu))s=s.replace(cpu,
                        'vec2 diagnosticCell=floor(vTerrainMaterialPosition.xz-0.5);'+
                        'bool diagnosticPureSwamp=terrainId(diagnosticCell).r==3.0 && terrainId(diagnosticCell+vec2(1.0,0.0)).r==3.0 && terrainId(diagnosticCell+vec2(0.0,1.0)).r==3.0 && terrainId(diagnosticCell+1.0).r==3.0;'+
                        'if(!diagnosticPureSwamp){'+cpu+'}');
                }
                if(['base','matte','color-only'].includes(mode)){
                    const wet='materialArm.g = mix(0.66, 0.22, wet);';
                    // The approved temporary Base PBR no longer has this override.
                    s=s.replace(wet,'/* diagnostic: keep source ARM roughness */');
                }
                if(mode==='legacy-only')s=s.replace('if (id < 0.5 || weight < 0.001) return;',
                    'if (id < 0.5 || weight < 0.001 || id == 3.0) return;');
                let effect='';
                if(mode==='matte')effect='if(id==3.0){materialArm.g=max(0.70,materialArm.g);}';
                if(mode==='color-only')effect='if(id==3.0){materialNormal=vec3(0.0,0.0,1.0);materialArm=vec3(1.0,0.95,0.0);}';
                if(mode==='replay-b2')effect=replay;
                if(effect)s=s.replace('color += materialColor * weight;',effect+String.fromCharCode(10)+'color += materialColor * weight;');
                shader.fragmentShader=s;
            };
        }});
        if(!materials.length)throw new Error('No diagnostic materials');
        window.setSwampDiagnostic=next=>{mode=next;materials.forEach(m=>m.needsUpdate=true);};
        window.setSwampReplay=text=>{replay=text;};
        const renderer=sceneModule.renderer,draw=renderer.render.bind(renderer);
        // Freeze actual drawing between captures; animation may run, but each
        // captured game frame explicitly restores the same celestial light.
        renderer.render=()=>{};
        window.drawSwampDiagnostic=()=>{
            sceneModule.setCelestialTime(720);renderer.shadowMap.needsUpdate=true;
            draw(sceneModule.scene,sceneModule.camera);
            const gl=renderer.getContext(),width=gl.drawingBufferWidth,height=gl.drawingBufferHeight;
            const pixels=new Uint8Array(256*256*4);
            gl.readPixels(Math.floor(width/2)-128,Math.floor(height/2)-128,256,256,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
            return Array.from(pixels);
        };
    })()`);
    await page.evaluate(`setSwampReplay(${JSON.stringify(b2)})`);
    const results={};
    for(const near of [false,true]){
        await page.evaluate(near
            ? 'sceneModule.controls.target.set(34,1,16);sceneModule.camera.position.set(35.25,5,21);sceneModule.controls.update()'
            : 'sceneModule.controls.target.set(34,1,16);sceneModule.camera.position.set(40.25,21,41);sceneModule.controls.update()');
        let reference;
        for(const mode of ['current','no-legacy','base','matte','color-only','legacy-only','replay-b2']){
            await page.evaluate(`setSwampDiagnostic('${mode}')`);
            const pixels=await page.evaluate('drawSwampDiagnostic()');
            if(mode==='current')reference=pixels;
            let changed=0,total=0,max=0;
            for(let i=0;i<pixels.length;i+=4)for(let c=0;c<3;c++){
                const delta=Math.abs(pixels[i+c]-reference[i+c]);total+=delta;max=Math.max(max,delta);if(delta)changed++;
            }
            const label=mode+'-'+(near?'near':'far');
            results[label]={changedChannels:changed,meanAbsoluteDifference:total/(256*256*3),maxDifference:max};
            fs.writeFileSync(path.join(output,label+'.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
            console.log('Swamp diagnostic:',label,JSON.stringify(results[label]));
        }
    }
    const errors=await page.evaluate('sceneModule.renderer.info.programs.filter(p=>p.diagnostics?.runnable===false).length');
    assert.equal(errors,0);
    assert.equal(results['no-legacy-near'].maxDifference,0,'legacy CPU color must not override the interior PBR swamp');
    const hasOldNoise=fs.readFileSync(path.resolve(__dirname,'../app/static/js/terrainMaterialLayer.js'),'utf8').includes('materialArm.g = mix(0.66, 0.22, wet)');
    if(hasOldNoise)assert.ok(results['base-near'].meanAbsoluteDifference>0);
    else assert.equal(results['base-near'].maxDifference,0,'approved Base PBR must retain source ARM');
    assert.deepEqual(hashes(),before,'production source files remain untouched');
    fs.writeFileSync(path.join(output,'measurements.json'),JSON.stringify({results,shaderErrors:errors,productionHashes:before},null,2)+'\n');
    console.log('PASS: isolated swamp diagnosis, fourteen fixed-camera game frames, unchanged production sources');
};
