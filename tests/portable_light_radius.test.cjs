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
