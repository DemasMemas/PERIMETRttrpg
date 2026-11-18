// Три настоящих фонаря в ночной Припяти: один ракурс, выключено/включено.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,request,token,lobby,offered,loc,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'lights');assert.ok(safeSession.verified);
    await page.call('Emulation.setDeviceMetricsOverride',{width:1920,height:1080,deviceScaleFactor:1,mobile:false});
    const templates=['Налобный фонарик','Фонарик','Улучшенный фонарик'].map(name=>{
        const item=offered.global.find(t=>t.name===name&&t.category!=='weapon_module');assert.ok(item,name);return item;});
    const items=templates.map((t,index)=>({id:'nearfield-'+index,templateId:t.id,name:t.name,category:t.category,
        quantity:1,weight:t.weight,volume:t.volume,attributes:structuredClone(t.attributes||{})}));
    const battery=offered.global.find(t=>t.name==='Батарейки');assert.ok(battery);
    const owner=await request(`/lobbies/${lobby.id}/characters`,{name:'Проверка ближнего света',data:{
        weapons:[{id:'light-test-rifle',name:'Проверочная винтовка',category:'weapon',weight:1,volume:6,damage:10,range:20}],
        inventory:{pockets:[...items,{id:'light-test-battery',templateId:battery.id,name:battery.name,category:battery.category,
            quantity:1,weight:battery.weight,volume:battery.volume,attributes:structuredClone(battery.attributes||{})}],backpack:[]}}},token);
    await request(loc+'/spawn_character',{character_id:owner.id,tile_x:2,tile_y:30},token);
    await request(loc+'/characters/'+owner.id+'/facing',{facing_x:0,facing_y:1},token,'PATCH');
    const detail=await request(loc,null,token,'GET');
    assert.equal(detail.sight_range,25);
    await request(loc,{lighting:{...detail.lighting,mode:'manual',ambient_intensity:.03,directional_intensity:0,background_color:"#0b1018"}},token,'PUT');
    await page.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');window.T=await import('three');window.FG=L.sceneGraphicsForTest();
        locationControls.enableDamping=false;return true})()`);
    const restoreView=()=>page.evaluate('locationControls.target.set(2.5,1,43);locationControls.object.position.set(-12,24,43);locationControls.update();true');
    await restoreView();
    const lamp = 'FG.scene.children.find(node=>node.isSpotLight&&node.decay<0&&node.intensity>0)';
    const setProbes = () => page.evaluate(`(()=>{
        const light=${lamp}, axis=light.target.position.clone().sub(light.position).normalize();
        const floor=FG.characters.get(${owner.id}).model.position.y+.025;
        const center=light.position.clone().addScaledVector(axis,(floor-light.position.y)/axis.y);
        const near=light.position.clone().lerp(center,.18);near.y=floor;
        const outside=center.clone();outside.x+=2;
        const forward=axis.clone().setY(0).normalize();
        const across=new T.Vector3(forward.z,0,-forward.x);
        const distant=[20,23,26].map(distance=>[-.5,0,.5].flatMap(longitudinal=>
            [-.4,0,.4].map(lateral=>light.position.clone().addScaledVector(forward,distance+longitudinal)
                .addScaledVector(across,lateral).setY(floor))));
        window.beamProbes=[center,near,outside,...distant];
        return true;
    })()`);
    const sample=(probes='beamProbes',includeAir=false)=>page.evaluate(`(()=>{
        const {renderer,scene,camera}=FG;
        const beam=scene.children.find(node=>node.name==='portable_flashlight_beam'&&node.visible);
        if(beam && !${includeAir})beam.visible=false;
        try {renderer.render(scene,camera);} finally {if(beam)beam.visible=true;}
        const gl=renderer.getContext(),out=[];
        const pixels=new Uint8Array(5*5*4);
        for(const group of ${probes}) {
            const points=Array.isArray(group)?group:[group];let brightness=0;
            for(const probe of points) {
                const point=probe.clone().project(camera);
                const x=Math.round((point.x+1)*.5*gl.drawingBufferWidth),y=Math.round((point.y+1)*.5*gl.drawingBufferHeight);
                if(x<3||y<3||x>=gl.drawingBufferWidth-3||y>=gl.drawingBufferHeight-3)throw Error('Проба вне кадра');
                gl.readPixels(x-2,y-2,5,5,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
                for(let i=0;i<pixels.length;i+=4)brightness+=(pixels[i]+pixels[i+1]+pixels[i+2])/3;
            }
            out.push(brightness/(25*points.length));
        }
        return out})()`);
    const shot=async name=>fs.writeFileSync(path.join(safeSession.artifacts,name+'.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    const performanceRows=[];
    const measure=async (state,overviewOnly=false)=>{
        const results=await page.evaluate(`(async()=>{
            const {renderer,camera,controls}=FG,stats=a=>{a.sort((x,y)=>x-y);return{median:a[Math.floor(a.length*.5)],p95:a[Math.floor(a.length*.95)]}};
            const gl=renderer.getContext(),ext=renderer.capabilities.isWebGL2&&gl.getExtension('EXT_disjoint_timer_query_webgl2');
            const out=[];for(const view of [{name:'near',position:[-7,12,31],target:[2.5,1,39]},{name:'tactical',position:[26,32,67],target:[18,1,37]},{name:'overview',position:[86,49,86],target:[36,1,36]},{name:'away',position:[-7,12,31],target:[-25,1,16]}]){
                if(${overviewOnly}&&view.name!=='overview')continue;
                controls.target.set(...view.target);camera.position.set(...view.position);controls.update();
                for(let i=0;i<25;i++)await new Promise(requestAnimationFrame);
                const cpu=[],frames=[],gpu=[],queries=[],original=renderer.render;let last;
                renderer.render=function(...args){const at=performance.now(),q=ext&&gl.createQuery();if(q)gl.beginQuery(ext.TIME_ELAPSED_EXT,q);
                    try{return original.apply(this,args)}finally{if(q){gl.endQuery(ext.TIME_ELAPSED_EXT);queries.push(q)}cpu.push(performance.now()-at)}};
                try{for(let i=0;i<100;i++)await new Promise(resolve=>requestAnimationFrame(t=>{if(last)frames.push(t-last);last=t;resolve()}));}
                finally{renderer.render=original;}
                if(ext){for(let i=0;i<10;i++)await new Promise(requestAnimationFrame);for(const q of queries){
                    if(gl.getQueryParameter(q,gl.QUERY_RESULT_AVAILABLE)&&!gl.getParameter(ext.GPU_DISJOINT_EXT))gpu.push(gl.getQueryParameter(q,gl.QUERY_RESULT)/1e6);gl.deleteQuery(q)}}
                out.push({view:view.name,frameMs:stats(frames),cpuMs:stats(cpu),gpuMs:gpu.length?stats(gpu):null,calls:renderer.info.render.calls,triangles:renderer.info.render.triangles});
            }return out;
        })()`);performanceRows.push({state,results});
        await restoreView();
    };
    const rows=[];
    let reservedSpots = false;
    for(const [index,item]of items.entries()){
        const operate=async(operation,extra={})=>{const fresh=await request('/lobbies/characters/'+owner.id,null,token,'GET');
            return request('/lobbies/characters/'+owner.id+'/portable-lights',{operation,path:['inventory','pockets',index],source_id:item.id,
                character_revision:fresh.data._revision,...extra},token);};
        if(index===0){
            await measure('off');
            const reservedSlots="FG.scene.children.filter(light=>light.isSpotLight&&light.decay<0&&light.intensity===0)";
            reservedSpots = await page.evaluate(`${reservedSlots}.filter(light=>light.visible).length===4`);
            if(reservedSpots){
                await page.evaluate(`${reservedSlots}.forEach(light=>{light.visible=false});true`);
                try{await measure('off-without-reserved',true);}
                finally{await page.evaluate(`${reservedSlots}.forEach(light=>{light.visible=true});true`);}
                await measure('off-reserved-repeat',true);
            }
        }
        await page.evaluate('(()=>{window.lampFrameGaps=[];window.lampFrameLast=performance.now();window.lampFrameGeneration=(window.lampFrameGeneration||0)+1;const generation=lampFrameGeneration;function lampFrame(t){if(generation!==lampFrameGeneration)return;lampFrameGaps.push(t-lampFrameLast);lampFrameLast=t;if(lampFrameGaps.length<5000)requestAnimationFrame(lampFrame)};requestAnimationFrame(lampFrame);return true})()');
        await operate('prepare',{mount:index===0?'head':'hand'});const programsBefore=await page.evaluate('FG.renderer.info.programs.length'),onStarted=Date.now();await operate('toggle');
        await page.until(`!!${lamp}&&FG.scene.children.some(node=>node.name==='portable_flashlight_beam'&&node.visible)`,'Включился луч из линзы');
        await setProbes();
        const on=await sample();const onMs=Date.now()-onStarted;const onGap=await page.evaluate('Math.max(0,...lampFrameGaps)');assert.ok(onGap<1000,item.name+': зависание кадра '+onGap);const programsAfter=await page.evaluate('FG.renderer.info.programs.length');await shot('lamp-'+index+'-on');
        if(index===0)await measure('on');
        const beams=await page.evaluate("FG.scene.children.filter(l=>l.isSpotLight&&l.decay<0&&l.intensity>0).map(l=>({angle:l.angle,range:l.distance,height:l.position.y,aspect:l.shadow.camera.aspect}))");
        assert.equal(beams.length,1);assert.equal(beams[0].range,detail.sight_range);
        const emitterHeight=await page.evaluate(`(()=>{const lamp=FG.scene.children.find(node=>node.isSpotLight&&node.intensity>0);
            return lamp.position.y-FG.characters.get(${owner.id}).model.position.y})()`);
        assert.ok(emitterHeight > (index===0 ? 1.5 : .65) && emitterHeight < 1.95,'Высота начала луча у модели');
        assert.ok(!await page.evaluate('FG.scene.children.some(node=>node.userData.portableEmitter)'), 'В воздухе нет маркера');
        if(index<2){
            await page.evaluate('locationControls.target.set(2.5,2.2,31);locationControls.object.position.set(-.5,4.5,30);locationControls.update();true');
            await shot(index===0?'emitter-close-head':'emitter-close-hand');
            await restoreView();
        }
        if(index===0){
            await page.evaluate('locationControls.object.position.set(-19,35,47);locationControls.update();true');
            const angleOn=await sample();await shot('lamp-head-second-angle');
            await operate('toggle');await page.until(`!${lamp}`,'Фонарь выключен');
            const angleOff=await sample();assert.ok(angleOn[0]-angleOff[0]>5,'Пол в конусе светлее с другого ракурса');
            await restoreView();await operate('toggle');
            await page.until(`!!${lamp}`,'Фонарь включён повторно');
            await page.evaluate(`(()=>{
                locationControls.target.set(2.5,1.3,34);
                locationControls.object.position.set(2,7,24);
                locationControls.update();
                const light=${lamp},axis=light.target.position.clone().sub(light.position).normalize();
                window.airProbes=[2,3,4].map(t=>light.position.clone().addScaledVector(axis,t));
                return true;
            })()`);
            const visibleAir=await sample('airProbes',true), emptyAir=await sample('airProbes');
            assert.ok(visibleAir.filter((v,i)=>v-emptyAir[i]>5).length>=2,'Луч от линзы виден над полом сзади');
            await shot('lamp-head-behind');
            await page.evaluate(`(()=>{
                const light=${lamp},ground=FG.characters.get(${owner.id}).model.position.y;
                window.underTerrainProbe=[light.position.clone().add(new T.Vector3(0,0,22)).setY(ground-1.3)];
                FG.controls.target.copy(underTerrainProbe[0]);
                FG.camera.position.set(-6,ground-1.15,underTerrainProbe[0].z-1);
                FG.controls.update();FG.camera.updateMatrixWorld(true);
                return true;
            })()`);
            const underground=await sample('underTerrainProbe',true), undergroundOff=await sample('underTerrainProbe');
            fs.writeFileSync(path.join(safeSession.artifacts,'lamp-terrain-shadow.json'),JSON.stringify({underground,undergroundOff}));
            assert.ok(Math.abs(underground[0]-undergroundOff[0])<3,'Рельеф перекрывает видимый луч под картой');
            await restoreView();
        }
        const offStarted=Date.now();await page.evaluate('lampFrameGaps=[];true');await operate('stow');
        await page.until(`!${lamp}`,'Источник убран');const offMs=Date.now()-offStarted,offGap=await page.evaluate('Math.max(0,...lampFrameGaps)');
        const off=await sample();await shot('lamp-'+index+'-off');
        fs.writeFileSync(path.join(safeSession.artifacts,'beam-probes-'+index+'.json'),JSON.stringify({off,on},null,2));
        assert.ok(on[0]-off[0]>5,item.name+': пол светлеет внутри конуса');
        assert.ok(Math.abs(on[1]-off[1])<4,item.name+': под лучом нет отдельного пятна');
        assert.ok(Math.abs(on[2]-off[2])<4,item.name+': сбоку от конуса пол не засвечен');
        assert.ok(on[3]-off[3]>8 && on[4]-off[4]>5,item.name+': пол освещён на 20 и 23 м');
        assert.ok(Math.abs(on[5]-off[5])<4,item.name+': за пределом 25 м яркость пола не меняется');
        assert.ok(offGap<1000,item.name+': зависание при выключении '+offGap);rows.push({name:item.name,off,on,beams,onMs,onGap,offMs,offGap,programsBefore,programsAfter});
    }
    const fresh=await request('/lobbies/characters/'+owner.id,null,token,'GET');
    await request('/lobbies/characters/'+owner.id+'/portable-lights',{operation:'prepare',path:['inventory','pockets',0],source_id:items[0].id,
        character_revision:fresh.data._revision,mount:'head'},token);
    const next=await request('/lobbies/characters/'+owner.id,null,token,'GET');
    await request('/lobbies/characters/'+owner.id+'/portable-lights',{operation:'toggle',path:['inventory','pockets',0],source_id:items[0].id,
        character_revision:next.data._revision},token);
    await page.until(`!!${lamp}`,'Налобник включён');
    await setProbes();
    const open=await sample();
    const wall=await request(loc+'/objects',{type:'wall',name:'Проверка тени фонаря',tile_x:2,tile_y:35,
        properties:{rotation:0,dimensions:{width:4,depth:.25,height:3},level:0}},token);
    await page.until(`L.sceneGraphicsForTest().roots.some(mesh=>mesh.userData.locationObjectId===${wall.id})`,'Настоящая стена в Припяти');
    const blocked=await sample();await shot('lamp-wall-shadow');
    assert.ok(open[0]-blocked[0]>5,'Стена перекрывает луч до пола');
    assert.ok(Math.abs(blocked[1]-open[1])<4,'Вне конуса пол сохраняет яркость');
    fs.writeFileSync(path.join(safeSession.artifacts,'wall-shadow.json'),JSON.stringify(blocked,null,2));
    const floorProperties={building_id:'lamp-storeys-test',rotation:0,dimensions:{width:4,depth:28,height:.12}};
    const floorIds=[];
    for(const level of [0,1]){
        const floor=await request(loc+'/objects',{type:'floor',name:'Перекрытие для проверки фонаря',
            tile_x:2,tile_y:42,properties:{...floorProperties,level}},token);
        floorIds.push(floor.id);
    }
    await page.until("FG.roots.filter(r=>r.userData.locationObject?.properties?.building_id==='lamp-storeys-test').length===2",
        'Оба этажа появились в сцене');
    await page.evaluate('L.setLocationViewLevel(1);true');
    await request(loc+'/spawn_character',{character_id:owner.id,tile_x:2,tile_y:30,floor_level:1,force_relocate:true},token);
    await page.until(`FG.characters.get(${owner.id}).floorLevel===1 && ${lamp}.position.y>4`,'Фонарь на верхнем этаже');
    const cut=await page.evaluate(`(()=>{
        const floor=FG.roots.find(r=>r.userData.locationObject?.properties?.building_id==='lamp-storeys-test'
            &&r.userData.locationObject.properties.level===1);
        const light=${lamp}, origin=light.position.clone();
        const probe=new T.Vector3(origin.x,floor.position.y-.055,origin.z+22);
        const wall=FG.roots.find(r=>r.userData.locationObjectId===${wall.id});
        let material;
        wall.traverse(mesh=>{if(mesh.isMesh&&!material)material=[mesh.material].flat()[0]});
        window.floorBeamTarget=new T.Mesh(new T.PlaneGeometry(.48,.075),material);
        floorBeamTarget.rotation.y=Math.PI;floorBeamTarget.position.copy(probe);
        floorBeamTarget.receiveShadow=true;FG.scene.add(floorBeamTarget);
        window.storeyDirectProbes=[probe];
        window.storeyAirProbes=[probe.clone().add(new T.Vector3(-.45,0,0))];
        FG.controls.target.set(2.5,2.9,probe.z);
        FG.camera.position.set(-2.5,floor.position.y-.015,probe.z-2);
        FG.controls.update();FG.camera.updateMatrixWorld(true);
        const t=(origin.y-floor.position.y)/(origin.y-probe.y);
        return {x:probe.x,z:origin.z+(probe.z-origin.z)*t};
    })()`);
    const panelLight = async () => {
        const direct=await sample('storeyDirectProbes');
        const air=await sample('storeyAirProbes',true), background=await sample('storeyAirProbes');
        return {direct:direct[0],air:air[0]-background[0]};
    };
    try{
        const covered=await panelLight();
        await shot('lamp-solid-floor');
        const cutaway=await page.evaluate(`(()=>{
            L.setLocationViewLevel(0);
            const floor=FG.roots.find(r=>r.userData.locationObjectId===${floorIds[1]});
            const hidden=!floor.visible;
            const casts=FG.shadowPass.run(()=>floor.visible&&floor.children.some(mesh=>mesh.castShadow));
            ${lamp}.shadow.needsUpdate=true;
            return {hidden,casts};
        })()`);
        assert.deepEqual(cutaway,{hidden:true,casts:true},'Скрытый при переключении этажа пол остаётся в карте тени');
        const coveredInCutaway=await panelLight();
        await page.evaluate('L.setLocationViewLevel(1);true');
        const opening=await request(loc+'/objects',{type:'floor_opening',name:'Проём для проверки фонаря',
            tile_x:Math.floor(cut.x),tile_y:Math.floor(cut.z),
            properties:{...floorProperties,level:1,dimensions:{width:2,depth:3,height:.05},
                offset_x:cut.x-Math.floor(cut.x)-.5,offset_z:cut.z-Math.floor(cut.z)-.5}},token);
        await page.until(`FG.roots.some(r=>r.userData.locationObjectId===${opening.id})&& !${lamp}.shadow.needsUpdate`,
            'Карта тени учитывает проём');
        const throughOpening=await panelLight();
        await shot('lamp-floor-opening');
        const lampState=await request('/lobbies/characters/'+owner.id,null,token,'GET');
        await request('/lobbies/characters/'+owner.id+'/portable-lights',{operation:'toggle',path:['inventory','pockets',0],source_id:items[0].id,
            character_revision:lampState.data._revision},token);
        await page.until(`!${lamp}`,'Фонарь выключен внутри здания');
        const noLight=await panelLight();
        fs.writeFileSync(path.join(safeSession.artifacts,'storey-light.json'),JSON.stringify({covered,coveredInCutaway,throughOpening,noLight},null,2));
        assert.ok(throughOpening.direct-noLight.direct>15,'Фонарь освещает поверхность через проём');
        assert.ok(throughOpening.air>5,'В проёме виден луч');
        assert.ok(Math.abs(covered.direct-noLight.direct)<4,'Сплошное перекрытие блокирует прямой свет');
        assert.ok(Math.abs(coveredInCutaway.direct-noLight.direct)<4,'Скрытое перекрытие блокирует прямой свет');
        assert.ok(covered.air<3,'Сплошное перекрытие блокирует видимый луч');
        await request(`/lobbies/${lobby.id}/locations/objects/${opening.id}`,null,token,'DELETE');
    }finally{
        await page.evaluate('FG.scene.remove(floorBeamTarget);floorBeamTarget.geometry.dispose();L.setLocationViewLevel(0);true');
    }
    await request(loc+'/spawn_character',{character_id:owner.id,tile_x:2,tile_y:30,floor_level:0,force_relocate:true},token);
    for(const id of floorIds.reverse())await request(`/lobbies/${lobby.id}/locations/objects/${id}`,null,token,'DELETE');
    const headlampState=await request('/lobbies/characters/'+owner.id,null,token,'GET');
    await request('/lobbies/characters/'+owner.id+'/portable-lights',{operation:'toggle',path:['inventory','pockets',0],source_id:items[0].id,
        character_revision:headlampState.data._revision},token);
    fs.writeFileSync(path.join(safeSession.artifacts,'nearfield.json'),JSON.stringify(rows,null,2));
    fs.writeFileSync(path.join(safeSession.artifacts,'light-performance.json'),JSON.stringify(performanceRows,null,2));
    const day=await request(loc,null,token,'GET');
    await request(loc,{lighting:{...day.lighting,mode:'manual',ambient_intensity:.7,directional_intensity:.85,background_color:'#a8b4b8'}},token,'PUT');
    await page.evaluate('locationControls.target.set(36,1,36);locationControls.object.position.set(86,49,86);locationControls.update();true');
    await shot('day-terrain-after-lights');
    const errors=await page.evaluate('FG.renderer.getContext().getError()');assert.equal(errors,0,'Ошибка графики после переключений');
    await page.evaluate(`window.openCharacterSheet(${owner.id},'equipment').then(()=>true)`);
    await page.evaluate("document.querySelector('.equipment-panel-tab[data-panel=detector]').click();true");
    for(const[width,height]of [[1920,1080],[1600,900],[1366,768],[1280,720]]){
        await page.call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
        await page.until("[...document.querySelectorAll('.portable-light-options')].filter(group=>group.offsetWidth>0).length===5",
            'Батарейки и крепления видны после обновления вкладки «Приборы»');
        const groups=await page.evaluate(`(()=>{const box=e=>{const r=e.getBoundingClientRect();return{x:r.x,y:r.y,right:r.right,bottom:r.bottom}};
            return [...document.querySelectorAll('.portable-light-options')].map(group=>({
                select:box(group.querySelector('select')),button:box(group.querySelector('button')),group:box(group),
                caption:box(group.parentElement.querySelector('strong')),label:group.querySelector('select').getAttribute('aria-label')}))})()`);
        assert.equal(groups.length,5);
        for(const group of groups){
            assert.ok(group.group.y>=group.caption.bottom,'Выбор ниже названия: '+group.label);
            assert.ok(group.select.right<=group.button.x,'Выбор рядом с кнопкой: '+group.label);
            assert.ok(Math.min(group.select.bottom,group.button.bottom)>Math.max(group.select.y,group.button.y),'Одна строка: '+group.label);
            assert.ok(group.button.right<=group.group.right+1&&group.group.right<=width,'Без горизонтального переполнения');
        }
        if(width===1920||width===1280)await shot('lamp-controls-'+width);
    }
    await page.call('Emulation.setDeviceMetricsOverride',{width:1920,height:1080,deviceScaleFactor:1,mobile:false});
    const sheetBox=()=>page.evaluate("(()=>{const r=document.querySelector('#character-sheet-modal .modal-content').getBoundingClientRect();return{x:r.x,y:r.y,width:r.width}})()");
    const beforeDrag=await sheetBox(),pointer={x:beforeDrag.x+beforeDrag.width*.5,y:beforeDrag.y+30};
    await page.call('Input.dispatchMouseEvent',{type:'mousePressed',...pointer,button:'left',clickCount:1});
    await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',x:pointer.x+90,y:pointer.y+60,button:'left',buttons:1});
    await page.call('Input.dispatchMouseEvent',{type:'mouseReleased',x:pointer.x+90,y:pointer.y+60,button:'left',clickCount:1});
    const afterDrag=await sheetBox();assert.equal(afterDrag.x,beforeDrag.x);assert.equal(afterDrag.y,beforeDrag.y);
    const multipleLights = {reservedSpots,textureUnits:await page.evaluate('FG.renderer.capabilities.maxTextures')};
    const saveMultipleLights = stage => {
        multipleLights.stage = stage;
        fs.writeFileSync(path.join(safeSession.artifacts,'four-lamps.json'),JSON.stringify(multipleLights,null,2));
    };
    await page.evaluate(`(()=>{
        const gl=FG.renderer.getContext();
        const samples={programQueries:[],renders:[]};window.multipleLampSamples=samples;
        for(const method of ['getProgramParameter','getProgramInfoLog','getActiveUniform','linkProgram']){
            const original=gl[method].bind(gl);
            gl[method]=(...args)=>{
                const start=performance.now();
                try{return original(...args)}finally{
                    const ms=performance.now()-start;if(ms>25)samples.programQueries.push({method,ms});
                }
            };
        }
        const render=FG.renderer.render;
        FG.renderer.render=function(...args){
            const start=performance.now(),lights=L.getLocationPortableLightDiagnostics().lights;
            try{return render.apply(this,args)}finally{
                const ms=performance.now()-start;if(ms>25)samples.renders.push({lights,ms});
            }
        };
        return true;
    })()`);
    saveMultipleLights('Включение второго фонаря первого персонажа');
    const lampRow=index=>`document.querySelector('.portable-light-row[data-light-id="nearfield-${index}"]')`;
    await page.evaluate(`${lampRow(1)}.querySelector('[data-light-operation=prepare]').click();true`);
    await page.until(`${lampRow(1)}?.textContent.includes('в руке')`,'Обычный фонарь подготовлен');
    await page.evaluate(`${lampRow(1)}.querySelector('[data-light-operation=toggle]').click();true`);
    await page.until(`${lampRow(1)}?.textContent.includes('включён')`,'Обычный фонарь включён');
    await page.evaluate(`${lampRow(2)}.querySelector('[data-light-operation=prepare]').click();true`);
    await page.until(`${lampRow(2)}?.textContent.includes('в руке')&&${lampRow(2)}?.textContent.includes('включён')&&!${lampRow(1)}?.textContent.includes('в руке')`,'Замена включённого фонаря одной кнопкой');
    const replaced=await request('/lobbies/characters/'+owner.id,null,token,'GET');
    assert.deepEqual(replaced.data.health.combatMeta.portableLights.filter(entry=>entry.mount==='hand').map(entry=>[entry.id,entry.on]),[['nearfield-2',true]]);
    await shot('lamp-replaced-with-one-click');
    multipleLights.samples=await page.evaluate('window.multipleLampSamples');
    saveMultipleLights('Два включённых источника');
    const helperItems=items.slice(0,2).map((item,index)=>({...item,id:'second-source-'+index}));
    const helper=await request(`/lobbies/${lobby.id}/characters`,{name:'Второй носитель света',data:{inventory:{pockets:helperItems,backpack:[]}}},token);
    await request(loc+'/spawn_character',{character_id:helper.id,tile_x:7,tile_y:30},token);
    saveMultipleLights('Включение источников второго персонажа');
    for(const [index,item] of helperItems.entries())for(const operation of ['prepare','toggle']){
        const state=await request('/lobbies/characters/'+helper.id,null,token,'GET');
        await request('/lobbies/characters/'+helper.id+'/portable-lights',{operation,path:['inventory','pockets',index],source_id:item.id,
            character_revision:state.data._revision,...(operation==='prepare'?{mount:index===0?'head':'hand'}:{})},token);
    }
    saveMultipleLights('Камера полного обзора');
    await page.evaluate('window.closeCharacterSheet();locationControls.target.set(36,1,36);locationControls.object.position.set(86,49,86);locationControls.update();true');
    saveMultipleLights('Четыре включённых источника');
    await page.until('L.getLocationPortableLightDiagnostics().lights===4','Четыре настоящих включённых источника');
    multipleLights.samples=await page.evaluate('window.multipleLampSamples');
    saveMultipleLights('Проверка шейдеров');
    const shaderErrors=await page.evaluate(`(async()=>{
        await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
        return FG.renderer.info.programs.filter(program=>program.diagnostics?.runnable===false)
            .map(program=>program.diagnostics.programLog.slice(0,200));
    })()`);
    assert.deepEqual(shaderErrors,[],'Несколько фонарей не ломают материалы');
    await shot('four-lamps-terrain');
    multipleLights.samples=await page.evaluate('window.multipleLampSamples');
    if(reservedSpots)assert.ok(multipleLights.samples.renders.every(({ms})=>ms<1000),
        'Переключение нескольких фонарей не должно останавливать отрисовку');
    saveMultipleLights('Готово');
    const activeOwner=await request('/lobbies/characters/'+owner.id,null,token,'GET');
    await request('/lobbies/characters/'+owner.id+'/portable-lights',{operation:'stow',path:['inventory','pockets',2],
        source_id:items[2].id,character_revision:activeOwner.data._revision},token);
    const setGlowView=z=>page.evaluate(`(async()=>{
        FG.controls.target.set(2.5,1,${z});FG.camera.position.set(-5,5,${z});FG.controls.update();
        await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));return true;
    })()`);
    await setGlowView(38);
    for(const [name,z] of [['ХИС',38],['Светящаяся палочка',54]]){
        const id='pan-glow-'+z;
        const actor=await request(`/lobbies/${lobby.id}/characters`,{name,data:{
            inventory:{pockets:[{id,name,category:'misc',quantity:1,weight:0,volume:1}]}}},token);
        await request(loc+'/spawn_character',{character_id:actor.id,tile_x:2,tile_y:z},token);
        const placed=await request('/lobbies/characters/'+actor.id,null,token,'GET');
        await request('/lobbies/characters/'+actor.id+'/portable-lights',{operation:'ignite',path:['inventory','pockets',0],
            source_id:id,character_revision:placed.data._revision},token);
    }
    const shadowState="FG.scene.children.filter(l=>l.isPointLight&&l.decay===.4&&l.intensity>0).map(l=>({range:l.distance,casts:l.castShadow,ready:!!l.shadow.map&&!l.shadow.needsUpdate})).sort((a,b)=>b.range-a.range)";
    await page.until(`(${shadowState}).length===2&&(${shadowState})[0].ready`,'ХИС получил карту тени');
    const views=await page.evaluate(`(()=>{
        const [his,stick]=FG.scene.children.filter(l=>l.isPointLight&&l.decay===.4&&l.intensity>0)
            .sort((a,b)=>b.distance-a.distance);
        const camera=FG.camera.clone(),projection=new T.Matrix4(),frustum=new T.Frustum();
        const sphere=new T.Sphere();
        let partial,clear;
        for(let z=38;z<=65;z+=.2){
            camera.position.set(-5,5,z);camera.lookAt(2.5,1,z);camera.updateMatrixWorld(true);
            frustum.setFromProjectionMatrix(projection.multiplyMatrices(camera.projectionMatrix,camera.matrixWorldInverse));
            const hisVisible=frustum.intersectsSphere(sphere.set(his.position,his.distance));
            const stickVisible=frustum.intersectsSphere(sphere.set(stick.position,stick.distance));
            if(hisVisible&&stickVisible&&!frustum.containsPoint(his.position))partial=z;
            if(stickVisible&&!frustum.intersectsSphere(sphere.set(his.position,his.distance+.25))){clear=z;break;}
        }
        return {partial,clear};
    })()`);
    assert.ok(views.partial&&views.clear&&views.clear-views.partial<1,'Край света проверяется коротким панорамированием');
    await setGlowView(views.partial);
    const partialShadow=await page.evaluate(shadowState);
    await setGlowView(views.clear);
    const returnedShadow=await page.evaluate(shadowState);
    await setGlowView(38);
    const firstShadow=await page.evaluate(shadowState);
    assert.equal(partialShadow[0].casts,true,'У ХИСа есть тень, пока в кадре видна часть радиуса');
    assert.ok(returnedShadow[1].casts&&returnedShadow[1].ready,'Палочка получила тень к следующему кадру');
    assert.ok(firstShadow[0].casts&&firstShadow[0].ready,'ХИС сразу восстанавливает тень при возвращении камеры');
    fs.writeFileSync(path.join(safeSession.artifacts,'glow-shadow-pan.json'),JSON.stringify({views,partialShadow,returnedShadow,firstShadow}));
    console.log('Ближний свет: три фонаря, настоящая ночная Припять, одинаковые камера и пробы земли; результат сохранён.');
};
