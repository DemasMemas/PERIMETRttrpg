const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {createLabelOcclusionIndex}=vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../app/static/js/locationLabelOcclusion.js'),'utf8').replace('export ','')+'\n;({createLabelOcclusionIndex})');

// Для проверки отбора достаточно луча вдоль X; преобразования выполняет Three.js.
class Vector3 {constructor(x=0){this.x=x;}distanceToSquared(v){return(this.x-v.x)**2;}}
class Matrix4 {constructor(){this.x=0;}multiplyMatrices(a,b){this.x=a.x+b.x;return this;}}
class Box3 {
    constructor(min=Infinity,max=-Infinity){Object.assign(this,{min,max});}
    copy(box){this.min=box.min;this.max=box.max;return this;}
    clone(){return new Box3(this.min,this.max);}
    applyMatrix4(matrix){this.min+=matrix.x;this.max+=matrix.x;return this;}
    union(box){this.min=Math.min(this.min,box.min);this.max=Math.max(this.max,box.max);return this;}
    containsPoint(point){return point.x>=this.min&&point.x<=this.max;}
}
const THREE={Vector3,Matrix4,Box3};
function mesh(min,max){
    const geometry={boundingBox:null,computations:0,computeBoundingBox(){this.computations++;this.boundingBox=new Box3(min,max);}};
    return{isMesh:true,visible:true,geometry,userData:{},matrixWorld:{x:0}};
}
function root(...children){return{visible:true,updateWorldMatrix(){},traverse(visit){children.forEach(visit);}};}
function picker(origin=0,far=10){return{far,calls:[],ray:{origin:new Vector3(origin),intersectBox(box,point){if(box.max<origin)return null;point.x=Math.max(origin,box.min);return point;}},intersectObject(mesh,recursive){this.calls.push(mesh);assert.equal(recursive,false);return[{object:mesh}];}};}

test('далёкие объекты и детали вне отрезка до метки не запускают raycast; границы переиспользуются',()=>{
    const distant=Array.from({length:1000},(_,i)=>root(mesh(20+i,21+i)));
    const near=mesh(4,5),past=mesh(40,41),group=root(near,past),p=picker();
    const index=createLabelOcclusionIndex(THREE,[...distant,group]);
    for(let i=0;i<5;i++)assert.equal(index.blocked(p,()=>true,()=>true),true);
    assert.equal(p.calls.length,5);assert.ok(p.calls.every(item=>item===near));
    assert.equal(near.geometry.computations,1);assert.equal(past.geometry.computations,1);
});
test('скрытое окружение и поверхности выбора пропускаются; видимость меняется без перестройки',()=>{
    const first=mesh(2,3),second=mesh(5,6),pickOnly=mesh(1,9);pickOnly.userData.pickOnly=true;
    const a=root(first),b=root(second),p=picker();a.visible=false;
    const index=createLabelOcclusionIndex(THREE,[root(pickOnly),a,b]);
    assert.equal(index.blocked(p,m=>m.visible,()=>true),true);assert.deepEqual(p.calls,[second]);
    a.visible=true;p.calls=[];assert.equal(index.blocked(p,m=>m.visible,()=>true),true);assert.deepEqual(p.calls,[first]);
    first.visible=false;second.visible=false;p.calls=[];assert.equal(index.blocked(p,m=>m.visible,()=>true),false);assert.equal(p.calls.length,0);
});
test('отвергнутый прозрачный хит не скрывает метку; луч изнутри границ тоже проверяется',()=>{
    const glass=mesh(-2,3),wall=mesh(7,8),p=picker(),index=createLabelOcclusionIndex(THREE,[root(glass,wall)]);
    assert.equal(index.blocked(p,()=>true,hit=>hit.object===wall),true);assert.deepEqual(p.calls,[glass,wall]);
    p.calls=[];assert.equal(index.blocked(p,()=>true,()=>false),false);
});
test('границы InstancedMesh включают преобразования всех экземпляров',()=>{
    const instances=mesh(0,1);Object.assign(instances,{isInstancedMesh:true,count:2,matrixWorld:{x:100},getMatrixAt(i,target){target.x=i*10;}});
    const index=createLabelOcclusionIndex(THREE,[root(instances)]),far=picker(0,10);
    assert.equal(index.blocked(far,()=>true,()=>true),false);assert.equal(far.calls.length,0);
    const inside=picker(109,3);assert.equal(index.blocked(inside,()=>true,()=>true),true);assert.deepEqual(inside.calls,[instances]);
});
