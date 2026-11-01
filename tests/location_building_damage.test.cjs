const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const load=()=>import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync(path.join(__dirname,'../app/static/js/locationBuildingDamage.js'),'utf8')).toString('base64'));
const {modules}=require('./location_furniture_fixture.cjs');
const pose={x:0,z:0,width:2,depth:2,axis:{x:1,z:0},normal:{x:0,z:1}};
test('Обломки закреплены только на уцелевшей стороне, соседние проёмы не образуют решётку',async()=>{
    const D=await load(),supports=[{...pose,x:0,z:-2}];
    const fragments=D.openingFracturePlan(pose,supports,'hole');
    assert.ok(fragments.length>=2);
    assert.ok(fragments.every(f=>f.z===-1&&f.angle===0&&f.length<=.60&&f.drop>0));
    assert.deepEqual(fragments,D.openingFracturePlan(pose,supports,'hole'));
    assert.deepEqual(D.openingFracturePlan(pose,[],'hole'),[]);
    assert.deepEqual(D.openingFracturePlan({...pose,x:2},supports,'hole'),[]);
    const rotated={...pose,x:10,z:10,axis:{x:0,z:-1},normal:{x:1,z:0}};
    assert.equal(D.openingFracturePlan(rotated,[{...rotated,x:8}], 'hole').length,fragments.length);
});
test('Рваный профиль остаётся в физических размерах стены и воспроизводится по seed',async()=>{
    const D=await load(),points=D.brokenWallOutline(2,.8,'wall');
    assert.deepEqual(points,D.brokenWallOutline(2,.8,'wall'));
    assert.notDeepEqual(points,D.brokenWallOutline(2,.8,'another'));
    assert.ok(points.every(([x,y])=>x>=-1&&x<=1&&y>=-.4&&y<=.4));
    const top=points.slice(2).map(p=>p[1]);assert.ok(new Set(top).size>4);
    assert.equal(Math.max(...top),.4);
    assert.equal(D.damagedWall({type:'wall',properties:{exterior_wall:true,dimensions:{height:.8},building_generation:{condition:'damaged'}}}),true);
    assert.equal(D.damagedWall({type:'wall',properties:{dimensions:{height:.8}}}),false);
    assert.equal(D.damagedOpening({type:'floor_opening',properties:{dimensions:{width:.9,depth:3.6},building_generation:{condition:'damaged'}}}),false);
});
test('Завалы привязаны к разрушенным стенам, падают на настоящий пол и сохраняются обычными объектами',async()=>{
    const {H,A}=await modules(),options={minX:2,minZ:2,width:12,depth:12,storeys:3,basements:1,buildingId:'damage',seed:'damage',wealth:'ordinary'};
    for(const condition of ['intact','worn','damaged','ruins']){
        const plan=H.planBuildingShell({...options,condition});assert.equal(plan.error,undefined);
        const debris=plan.objects.filter(o=>o.properties.structural_debris);
        if(['intact','worn'].includes(condition)){assert.equal(debris.length,0);continue;}
        assert.ok(debris.length>0&&debris.length<=48);
        for(const o of debris){
            assert.equal(o.type,'household_rubble');assert.equal(o.properties.passable,true);
            const p=A.structurePose(o),level=A.structureLevel(o),source=o.properties.structural_debris;
            assert.ok(level<=source.source_level);
            assert.ok(Math.hypot(p.x-source.source_x,p.z-source.source_z)<.5);
            const supported=plan.objects.filter(f=>f.type==='floor'&&A.structureLevel(f)===level).some(f=>{
                const q=A.floorVisualPose(f,plan.objects),dx=p.x-q.x,dz=p.z-q.z;
                return A.floorSlabParts(f,plan.objects).some(r=>Math.abs(dx-r.x)<=r.width/2&&Math.abs(dz-r.z)<=r.depth/2);
            });assert.ok(supported,'Завал не висит над проёмом');
        }
        assert.deepEqual(plan,H.planBuildingShell({...options,condition}));
    }
});

test('Частично разрушенные перегородки старых планировок получают сколы, целые стены и перемычки сохраняются',async()=>{
    const {damagedWall}=await load();
    const wall=(condition,height)=>({type:'wall',properties:{visual_asset:'inner_wall',dimensions:{height},layout_generation:{version:1,condition,seed:'old'}}});
    for(const condition of ['damaged','ruins']){
        for(const height of [.5,1.2,1.49,2.3])assert.equal(damagedWall(wall(condition,height)),true);
        assert.equal(damagedWall(wall(condition,2.4)),false);
        assert.equal(damagedWall({...wall(condition,.9),properties:{...wall(condition,.9).properties,roof_wall:{}}}),false);
    }
    for(const condition of ['intact','worn'])assert.equal(damagedWall(wall(condition,1.2)),false);
    const {H,I}=await modules(),shell=H.planBuildingShell({minX:2,minZ:2,width:16,depth:16,storeys:1,buildingId:'b',seed:'partitions',condition:'intact'});
    let partial=0;
    for(let seed=0;seed<8;seed++){
        const plan=I.planBuildingInterior(shell.objects,{buildingId:'b',level:0,condition:'ruins',seed:String(seed)});
        assert.equal(plan.error,undefined);
        for(const o of plan.objects.filter(o=>o.type==='wall')){
            if(o.properties.dimensions.height<2.4){partial++;assert.equal(o.properties.structural_damage.version,1);assert.ok(o.properties.structural_damage.seed);}
            else assert.equal(o.properties.structural_damage,undefined);
        }
    }
    assert.ok(partial>0);
});
