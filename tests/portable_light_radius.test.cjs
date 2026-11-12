const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

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
        constructor(color,intensity,distance){this.distance=distance;created.push(this);this.shadow={mapSize:{set(){}},camera:{}};
            this.position={set(){}};}
    }
    const scene={add(){},remove(){}};
    const renderer=module.namespace.createPortableLightRenderer({PointLight},scene);
    renderer.update([],new Map(),[
        {id:'his',radial:true,range:8,x:1,y:1,z:1},
        {id:'stick',radial:true,range:5,x:2,y:1,z:2},
    ]);
    assert.deepEqual(created.map(light=>light.distance),[8,5]);
});

test('ближний свет сохраняет горизонтальную ширину, камеру тени и прежний обработчик материала',async()=>{
    const three=new vm.SourceTextModule(fs.readFileSync(path.join(__dirname,'../tmp/browser-vendor/three.module.js'),'utf8'));
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
    assert.ok(Math.abs(2*35*Math.tan(light.shadow.camera.fov*Math.PI/360)*light.shadow.camera.aspect-5)<1e-8);
    assert.ok(Math.atan(1.4/1.5)<light.angle,'В ближнюю землю попадает вертикальная часть луча');
    const shader={uniforms:{},vertexShader:T.ShaderLib.standard.vertexShader,fragmentShader:T.ShaderLib.standard.fragmentShader};
    material.onBeforeCompile(shader);
    const camera=new T.PerspectiveCamera();camera.updateMatrixWorld();scene.onBeforeRender(null,scene,camera);
    assert.equal(shader.previousHandler,true);assert.ok(shader.fragmentShader.includes('portableBeamMask'));
    assert.match(shader.fragmentShader, /portableSpotShadow\( spotShadowMap\[/);
    assert.match(shader.fragmentShader, /getShadow\( directionalShadowMap\[/);
    material.onBeforeCompile(shader);
    assert.equal(shader.fragmentShader.match(/float portableSpotShadow\(/g).length, 1);
    for (const position of [[0,0,0],[4,8,12],[-5,3,2]]) {
        camera.position.set(...position);camera.lookAt(0,1,0);camera.updateMatrixWorld();
        scene.onBeforeRender(null,scene,camera);
        const expected=new T.Vector3().subVectors(light.position,light.target.position);
        expected.y=0;expected.transformDirection(camera.matrixWorldInverse);
        assert.ok(shader.uniforms.portableBeamForwards.value[0].distanceTo(expected)<1e-10,
            'Направление луча сохраняется при движении и повороте камеры');
    }
    assert.equal(light.decay,-1,'Затухание задаёт профиль без перебора источников в пикселе');
    assert.equal(shader.uniforms.portableBeamShapes.value[0].x,Math.cos(Math.atan(2.5/35)));
    assert.equal(shader.uniforms.portableBeamShapes.value[0].z,35);
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
        'Радиальный источник получает свой слот тени');
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
    const patrol=Array.from({length:15},(_,i)=>({character_id:i+10,posture:'standing',facing_x:0,facing_y:1,
        portable_lights:[{id:'patrol-'+i,mount:'head',range:20,end_width:5}]}));
    for(const actor of patrol)models.set(actor.character_id,{model});
    renderer.update(patrol,models,[{id:'stick',radial:true,range:5,x:1,y:1,z:1},{id:'his',radial:true,range:8,x:2,y:1,z:2}]);
    assert.deepEqual({...renderer.diagnostics},{lights:17,spots:15,radial:2,overflow:11,shadows:4});
    assert.equal(shader.uniforms.portableExtraCount.value,11);
    assert.match(shader.fragmentShader,/extraIndex<portableExtraCount/);
    scene.onBeforeRender(null,scene,camera);
    assert.equal(shader.uniforms.portableExtraPosition.value[10].w,20);
    assert.equal(light.intensity,11.5);renderer.dispose();
    assert.equal(material.onBeforeCompile,previous);assert.equal(renderer.count,0);
});

test('подготовка фонарей сохраняет объединение деталей и исключает неизвестные шейдеры',async()=>{
    const three=new vm.SourceTextModule(fs.readFileSync(path.join(__dirname,'../tmp/browser-vendor/three.module.js'),'utf8'));
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
