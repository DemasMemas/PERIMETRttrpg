const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

test('подпись горящего ХИСа показывает оставшиеся минуты и выбранный цвет', async () => {
    const module = new vm.SourceTextModule(fs.readFileSync(path.join(__dirname, '../app/static/js/portableLights.js'), 'utf8'));
    await module.link(specifier => {
        const name = specifier === './api.js' ? 'Server' : 'rememberEquipmentChoice';
        return new vm.SyntheticModule([name], function () { this.setExport(name, {}); });
    });
    await module.evaluate();
    const { activeGlowLabel } = module.namespace;
    assert.equal(activeGlowLabel({ activated: true, remaining_seconds: 126, color: '#68beff' }),
        'Осталось гореть ещё 3 мин · цвет: голубой');
    assert.equal(activeGlowLabel({ activated: true, remaining_seconds: 1 }),
        'Осталось гореть ещё 1 мин · цвет: зелёный');
    assert.equal(activeGlowLabel({ activated: true, remaining_seconds: 0, color: '#68beff' }), '');
    assert.equal(activeGlowLabel({ activated: false, remaining_seconds: 126 }), '');
});

test('сцена использует радиус ХИС 8 м вместо радиуса палочки 5 м',async()=>{
    const module=new vm.SourceTextModule(fs.readFileSync(path.join(__dirname,'../app/static/js/portableLights.js'),'utf8'));
    await module.link(async specifier=>{
        const stub=new vm.SyntheticModule(specifier==='./api.js'?['Server']:['rememberEquipmentChoice'],function(){
            this.setExport(specifier==='./api.js'?'Server':'rememberEquipmentChoice',{});
        });
        return stub;
    });
    await module.evaluate();
    const created=[];
    class PointLight {
        constructor(color,intensity,distance){this.isPointLight=true;this.distance=distance;created.push(this);this.shadow={mapSize:{set(){}},camera:{}};
            this.position={set(){}};}
    }
    const scene={add(){},remove(){}};
    const renderer=module.namespace.createPortableLightRenderer({PointLight},scene);
    renderer.update([],new Map(),[
        {id:'his',mount:'ground',radial:true,range:8,x:1,y:1,z:1},
        {id:'stick',mount:'ground',radial:true,range:5,x:2,y:1,z:2},
        {id:'aura',radial:true,ignition_aura:true,range:4,x:3,y:1,z:3},
    ]);
    assert.deepEqual(created.map(light=>light.distance),[8,5,4]);
    assert.deepEqual(created.map(light=>light.castShadow),[true,true,true]);
    assert.ok(created.slice(0,2).every(light=>light.shadow.radius>1 && !light.shadow.autoUpdate));
});

test('луч, прямой свет и камера тени используют один конус',async()=>{
    const three=new vm.SourceTextModule(fs.readFileSync(path.join(__dirname,'../app/static/vendor/three-0.162.0/build/three.module.min.js'),'utf8'));
    await three.link(()=>{throw Error('Неожиданный импорт Three');});await three.evaluate();
    const module=new vm.SourceTextModule(fs.readFileSync(path.join(__dirname,'../app/static/js/portableLights.js'),'utf8'));
    await module.link(async specifier=>new vm.SyntheticModule(specifier==='./api.js'?['Server']:['rememberEquipmentChoice'],function(){
        this.setExport(specifier==='./api.js'?'Server':'rememberEquipmentChoice',{});
    }));await module.evaluate();
    const T=three.namespace,scene=new T.Scene(),model=new T.Object3D(),material=new T.MeshStandardMaterial();
    const previous=shader=>{shader.previousHandler=true;};material.onBeforeCompile=previous;
    scene.add(new T.Mesh(new T.PlaneGeometry(2,2),material));model.position.y=1;scene.add(model);
    const renderer=module.namespace.createPortableLightRenderer(T,scene);
    renderer.update([{character_id:1,posture:'standing',facing_x:0,facing_y:1,
        portable_lights:[{id:'lamp',mount:'head',range:35,end_width:5}]}],new Map([[1,{model}]]));
    const light=scene.children.find(child=>child.isSpotLight);assert.equal(light.distance,35);
    light.shadow.updateMatrices(light);
    assert.ok(Math.abs(light.shadow.camera.fov * Math.PI / 360 - light.angle) < 1e-10);
    assert.equal(light.shadow.camera.aspect, 1);
    assert.equal(light.shadow.camera.far, light.distance);
    assert.equal(light.shadow.portableOwner, model);
    const shader={uniforms:{},vertexShader:T.ShaderLib.standard.vertexShader,fragmentShader:T.ShaderLib.standard.fragmentShader};
    material.onBeforeCompile(shader);
    const camera=new T.PerspectiveCamera();camera.updateMatrixWorld();scene.onBeforeRender(null,scene,camera);
    assert.equal(shader.previousHandler,true);
    assert.match(shader.fragmentShader, /portableBeamSurfaceFalloff\(lightDistance \* angleCos \/ spotLight.distance\)/);
    assert.doesNotMatch(shader.fragmentShader, /portableBeamForwards|portableBeamMask/);
    assert.match(shader.fragmentShader, /portableSpotShadow\( spotShadowMap\[/);
    assert.match(shader.fragmentShader, /getShadow\( directionalShadowMap\[/);
    material.onBeforeCompile(shader);
    assert.equal(shader.fragmentShader.match(/float portableSpotShadow\(/g).length, 1);
    assert.equal(light.decay,-1);
    assert.equal(light.angle, Math.atan(2.5/35));
    assert.equal(renderer.count,1,'Активен один источник');
    assert.equal(scene.children.filter(node=>node.isSpotLight).length,4,'Места подготовлены заранее');
    const version=material.version;
    renderer.update([],new Map());assert.equal(renderer.count,0);
    assert.equal(scene.children.filter(node=>node.isSpotLight).length,4,'Выключение не меняет число графических мест');
    assert.equal(material.version,version,'Выключение не пересобирает материал');
    const prepared=[];
    const compile = root => prepared.push(root.children.filter(node=>node.isSpotLight&&node.visible).length);
    await renderer.warm({capabilities:{maxTextures:32},compile},null);
    assert.deepEqual(prepared,[0],'Прогрев не компилирует неиспользуемый прожектор');
    prepared.length=0;
    const modern = {capabilities:{maxTextures:16},async compileAsync(root){compile(root);}};
    await renderer.warm(modern,null);
    assert.deepEqual(prepared,[4],'Прогревается тот же набор прожекторов, который получит рендерер');
    assert.ok(scene.children.filter(node=>node.isSpotLight).every(node=>node.visible&&node.intensity===0
        &&!node.shadow.autoUpdate&&!node.shadow.needsUpdate),'Выключенные прожекторы не светят и не обновляют тени');
    assert.ok(!scene.children.some(node=>node.userData.portableEmitter&&node.visible));
    renderer.update([{character_id:1,posture:'standing',facing_x:0,facing_y:1,
        portable_lights:[{id:'lamp',mount:'head',range:35,end_width:5}]}],new Map([[1,{model}]]));
    assert.equal(material.version,version,'Повторное включение не пересобирает материал');
    assert.equal(light.position.y,2.65,'Луч по-прежнему начинается на высоте головы');
    assert.ok(!scene.children.some(node=>node.userData.portableEmitter),'Включение не создаёт видимую точку');
    const actors = [{character_id:1,posture:'standing',facing_x:0,facing_y:1,
        portable_lights:[{id:'lamp',mount:'head',range:35,end_width:5}]}];
    const models = new Map([[1,{model}]]);
    renderer.update(actors,models,[{id:'glow',radial:true,x:1,y:1,z:1}]);
    assert.equal(scene.children.filter(node=>node.isSpotLight&&node.visible).length,1,
        'При радиальном источнике пустые прожекторы скрываются');
    assert.ok(scene.children.filter(node=>node.isPointLight).every(node=>node.castShadow),
        'Место для тени сохраняется у ХИСа рядом с фонарём');
    renderer.update(actors,models);
    assert.equal(scene.children.filter(node=>node.isSpotLight&&node.visible).length,4);
    renderer.useSeasonalTerrain();
    assert.equal(scene.children.filter(node=>node.isSpotLight&&node.visible).length,1,
        'В зимней сцене 16 текстурных слотов используются только активными фонарями');
    models.get(1).posture='prone';
    model.position.set(4,2,6);
    renderer.updatePositions();
    assert.ok(light.position.distanceTo(new T.Vector3(4,2.32,6.8))<1e-8,'Начало луча у лба и следует за моделью');
    assert.equal(light.distance,35);
    const rig = new T.Group(), emitter = new T.Mesh(new T.BoxGeometry(.02, .02, .02), new T.MeshBasicMaterial());
    emitter.name = 'gear_device_headlamp';
    emitter.material.name = 'device_headlamp_emitter';
    emitter.position.set(0, .4, .8);
    rig.add(emitter);
    model.add(rig);
    models.get(1).humanAppearanceModel = rig;
    actors[0].portable_lights[0].visual_model = 'headlamp';
    renderer.update(actors, models);
    const beam = scene.getObjectByName('portable_flashlight_beam');
    assert.ok(beam.visible);
    assert.ok(beam.position.distanceTo(light.position) < 1e-10);
    assert.ok(light.position.distanceTo(emitter.getWorldPosition(new T.Vector3())) < 1e-10);
    const beamAxis = new T.Vector3(0, 1, 0).applyQuaternion(beam.quaternion);
    assert.ok(beamAxis.distanceTo(light.target.position.clone().sub(light.position).normalize()) < 1e-10);
    assert.equal(beam.scale.y, light.distance);
    assert.ok(Math.abs(beam.scale.x - Math.tan(light.angle) * light.distance) < 1e-10);
    assert.match(beam.material.fragmentShader, /portableBeamFalloff\(distanceAlong\)/);
    assert.match(beam.material.fragmentShader, /normalize\(entryPoint - eyeInCone\)/);
    const patrol=Array.from({length:15},(_,i)=>({character_id:i+10,posture:'standing',facing_x:0,facing_y:1,
        portable_lights:[{id:'patrol-'+i,mount:'head',range:20,end_width:5}]}));
    for(const actor of patrol)models.set(actor.character_id,{model});
    renderer.update(patrol,models,[{id:'stick',radial:true,range:5,x:1,y:1,z:1},{id:'his',radial:true,range:8,x:2,y:1,z:2}]);
    assert.deepEqual({...renderer.diagnostics},{lights:17,spots:15,radial:2,overflow:11,shadows:4});
    assert.equal(shader.uniforms.portableExtraCount.value,11);
    assert.match(shader.fragmentShader,/extraIndex<portableExtraCount/);
    scene.onBeforeRender(null,scene,camera);
    assert.equal(shader.uniforms.portableExtraPosition.value[10].w,20);
    assert.equal(shader.uniforms.portableExtraCone.value[10].x, Math.cos(Math.atan(2.5/20)));
    assert.equal(light.intensity,11.5);

    const glows = [0, 2, 4, 6, 12.4].map(x=>({id:`glow-${x}`,radial:true,range:1.5,x,y:1,z:1}));
    const focus = new T.Vector3(0, 1, 1);
    const sideView = new T.OrthographicCamera(-5, 5, 5, -5, .1, 20);
    sideView.position.set(0, 1, 10);
    renderer.update([],new Map(),glows,sideView,focus);
    const shadowed = () => scene.children.filter(node=>node.isPointLight&&node.castShadow).map(node=>node.position.x).sort((a,b)=>a-b);
    assert.deepEqual(shadowed(),[0,2,4,6]);
    sideView.position.x=6.3;
    focus.x=6.3;
    renderer.updatePositions(sideView,focus);
    assert.deepEqual(shadowed(),[0,2,4,6],'Тень остаётся, пока в кадре край освещённой области');
    sideView.position.x=6.9;
    renderer.updatePositions(sideView,focus);
    assert.deepEqual(shadowed(),[2,4,6,12.4],'Перенос тени происходит сразу даже при малом движении камеры');
    assert.equal(scene.children.find(node=>node.isPointLight&&node.position.x===12.4).shadow.needsUpdate,true);
    renderer.dispose();
    assert.equal(material.onBeforeCompile,previous);assert.equal(renderer.count,0);
});

test('точка света следует за костью, перестановкой крепления и предметом на земле', async () => {
    const three = new vm.SourceTextModule(fs.readFileSync(path.join(__dirname,
        '../app/static/vendor/three-0.162.0/build/three.module.min.js'), 'utf8'));
    await three.link(() => { throw Error('Неожиданный импорт Three'); });
    await three.evaluate();
    const module = new vm.SourceTextModule(fs.readFileSync(path.join(__dirname, '../app/static/js/portableLights.js'), 'utf8'));
    await module.link(specifier => {
        const symbol = specifier === './api.js' ? 'Server' : 'rememberEquipmentChoice';
        return new vm.SyntheticModule([symbol], function () { this.setExport(symbol, {}); });
    });
    await module.evaluate();
    const T = three.namespace, scene = new T.Scene(), model = new T.Group(), rig = new T.Group();
    const bone = new T.Bone();
    const geometry = new T.BoxGeometry(.02, .03, .12).translate(-.20, .05, .01);
    const vertexCount = geometry.attributes.position.count;
    geometry.setAttribute('skinIndex', new T.Uint16BufferAttribute(new Uint16Array(vertexCount * 4), 4));
    const weights = new Float32Array(vertexCount * 4);
    for (let i = 0; i < weights.length; i += 4) weights[i] = 1;
    geometry.setAttribute('skinWeight', new T.Float32BufferAttribute(weights, 4));
    const emitter = new T.SkinnedMesh(geometry, new T.MeshStandardMaterial());
    emitter.material.name = 'device_his_hand_emitter';
    emitter.name = 'gear_device_his_hand';
    scene.add(model);
    model.add(rig);
    rig.add(bone, emitter);
    emitter.bind(new T.Skeleton([bone]));
    const renderer = module.namespace.createPortableLightRenderer(T, scene);
    const models = new Map([[1, { model, humanAppearanceModel: rig }]]);
    const source = { id: 'his', mount: 'hand', radial: true, range: 8, visual_model: 'his_hand' };
    const characters = [{ character_id: 1, portable_lights: [source] }];
    const point = () => scene.children.find(node => node.isPointLight);
    const position = () => point().position;
    renderer.update(characters, models);
    assert.ok(position().distanceTo(new T.Vector3(-.20, .05, .01)) < 1e-6);
    bone.position.set(.15, .55, -.1);
    model.position.set(2, 1, 3);
    renderer.updatePositions();
    assert.ok(position().distanceTo(new T.Vector3(1.95, 1.60, 2.91)) < 1e-6);
    point().shadow.needsUpdate = false;
    renderer.updatePositions();
    assert.equal(point().shadow.needsUpdate, false, 'Неподвижный источник использует готовую карту');
    bone.position.x += .001;
    renderer.updatePositions();
    assert.equal(point().shadow.needsUpdate, true, 'Карта обновляется в кадре даже при мелком движении руки');
    point().shadow.needsUpdate = false;
    rig.userData.shadowPoseRevision = 1;
    renderer.updatePositions();
    assert.equal(point().shadow.needsUpdate, true, 'Изменение силуэта в движении обновляет карту и без сдвига источника');

    const beltPart = new T.Mesh(geometry, new T.MeshStandardMaterial());
    beltPart.material.name = 'device_his_belt_emitter';
    beltPart.name = 'belt_gear_device_his_belt';
    rig.add(beltPart);
    beltPart.position.set(-.1, .8, .2);
    renderer.update([{ character_id: 1, portable_lights: [{ ...source, mount: 'belt', visual_model: 'his_belt' }] }], models);
    assert.ok(position().distanceTo(new T.Vector3(1.70, 1.85, 3.21)) < 1e-6);

    beltPart.removeFromParent();
    scene.add(beltPart);
    beltPart.position.set(4, .1, 5);
    renderer.update([], models, [{ ...source, mount: 'ground', groundEmitter: beltPart }]);
    assert.ok(position().distanceTo(new T.Vector3(3.80, .15, 5.01)) < 1e-6);
    renderer.dispose();
});

test('подготовка фонарей сохраняет объединение деталей и исключает неизвестные шейдеры',async()=>{
    const three=new vm.SourceTextModule(fs.readFileSync(path.join(__dirname,'../app/static/vendor/three-0.162.0/build/three.module.min.js'),'utf8'));
    await three.link(()=>{throw Error('Неожиданный импорт');});await three.evaluate();
    const portable=new vm.SourceTextModule(fs.readFileSync(path.join(__dirname,'../app/static/js/portableLights.js'),'utf8'));
    await portable.link(async specifier=>new vm.SyntheticModule(specifier==='./api.js'?['Server']:['rememberEquipmentChoice'],function(){
        this.setExport(specifier==='./api.js'?'Server':'rememberEquipmentChoice',{});
    }));await portable.evaluate();
    const batchModule=new vm.SourceTextModule(fs.readFileSync(path.join(__dirname,'../app/static/js/locationRenderBatches.js'),'utf8'));
    await batchModule.link(async()=>new vm.SyntheticModule(['isFireObject'],function(){this.setExport('isFireObject',()=>false);}));await batchModule.evaluate();
    const T=three.namespace,scene=new T.Scene(),roots=[];
    for(let i=0;i<3;i++){
        const root=new T.Group(),material=new T.MeshStandardMaterial({color:i===0?'red':'green'});
        if(i===2)material.onBeforeCompile=shader=>{shader.unrecognised=true};
        root.position.x=i*2;root.add(new T.Mesh(new T.BoxGeometry(1,1,1),material));roots.push(root);scene.add(root);
    }
    const lamps=portable.namespace.createPortableLightRenderer(T,scene);lamps.prepare();
    const fieldModule=new vm.SourceTextModule(fs.readFileSync(path.join(__dirname,'../app/static/js/locationDirectionalLightFieldPrototype.js'),'utf8'));
    await fieldModule.link(()=>{throw Error('Неожиданный импорт поля');});await fieldModule.evaluate();
    // Реальный порядок загрузки: ранний переносной профиль снимается,
    // подключается статическое поле, переносной профиль возвращается снаружи.
    for(const root of roots){const material=root.children[0].material;
        assert.ok(lamps.detachMaterial(material));
        fieldModule.namespace.applyDirectionalLightFieldMaterial(material,{id:123},{levels:[{id:0}]},0);
        lamps.attachMaterial(material);
    }
    const translucent=new T.Mesh(new T.BoxGeometry(),new T.MeshStandardMaterial({transparent:true,opacity:.6}));
    const translucentRoot=new T.Group();translucentRoot.add(translucent);roots.push(translucentRoot);scene.add(translucentRoot);
    let rootReads=0;
    const batches=batchModule.namespace.createLocationRenderBatches(T,scene,()=>{rootReads++;return roots;});batches.prepare();
    assert.equal(scene.children.filter(mesh=>mesh.userData.locationRenderBatch).length,1);
    assert.ok(!roots[0].children[0].visible&&!roots[1].children[0].visible);
    assert.ok(roots[2].children[0].visible,'Неизвестный обработчик не объединяется');
    batches.restore();rootReads=0;translucent.material.opacity=.4;batches.prepare();
    assert.equal(rootReads,1,'Изменение прозрачности крыши не запускает повторную группировку непрозрачных объектов');
    batches.restore();assert.ok(roots.every(root=>root.children[0].visible));batches.dispose();lamps.dispose();
    assert.ok(roots.every(root=>!root.children[0].material.userData.portableLightBeam));
});
