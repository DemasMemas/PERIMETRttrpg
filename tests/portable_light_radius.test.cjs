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
    assert.equal(shader.previousHandler,true);assert.ok(shader.fragmentShader.includes('portableBeamMask'));
    assert.equal(shader.uniforms.portableBeamOrigins.value[0].w,Math.atan(2.5/35));
    assert.equal(renderer.count,1,'Дополнительных источников нет');renderer.dispose();
    assert.equal(material.onBeforeCompile,previous);assert.equal(renderer.count,0);
});
