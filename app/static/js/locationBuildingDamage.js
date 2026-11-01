// Детерминированные небольшие обломки. Ни опорные поверхности, ни коллизии
// здесь не создаются: геометрия следует уже сохранённым разрушениям.
function randomFor(seed) {
    let state=2166136261;
    for(const char of String(seed))state=Math.imul(state^char.charCodeAt(0),16777619);
    return ()=>{state=(Math.imul(state,1664525)+1013904223)>>>0;return state/4294967296;};
}

export function damagedWall(object) {
    const p=object?.properties||{};
    return Boolean(object?.type==='wall'&&!p.roof_wall&&(p.structural_damage?.version===1
        || p.exterior_wall&&['damaged','ruins'].includes(p.building_generation?.condition)
            &&Number(p.dimensions?.height)<1.4
        || p.layout_generation?.version===1&&['damaged','ruins'].includes(p.layout_generation.condition)
            &&Number(p.dimensions?.height)>0&&Number(p.dimensions.height)<2.4));
}

export function damagedOpening(object) {
    const p=object?.properties||{},d=p.dimensions||{};
    return object?.type==='floor_opening'&&(p.structural_damage?.version===1
        || ['damaged','ruins'].includes(p.building_generation?.condition)&&d.width>=1.2&&d.depth>=1.2);
}

export function brokenWallOutline(width,height,seed) {
    const random=randomFor(seed),count=Math.max(4,Math.ceil(width/.34)),top=[],step=width/count;
    const peak=Math.floor(random()*count);
    // Короткие полки и сколы вместо одинаковых острых зубцов по всей стене.
    for(let i=0;i<count;i++){
        const y=-height/2+height*(i===peak?1:.43+.52*random()),x=-width/2+step*i;
        top.push([x+(i===0?0:step*.12*random()),y],
            [x+step*(i===count-1?1:.78+.12*random()),y-height*random()*.05]);
    }
    return [[-width/2,-height/2],[width/2,-height/2],...top.reverse()];
}

// supports содержат только реальные части перекрытий, уже с вырезанными
// лестницами и проёмами. Общие границы соседних дыр не дают обломков.
export function openingFracturePlan(pose,supports,seed) {
    const random=randomFor(seed),fragments=[];
    const supported=(x,z)=>{
        const wx=pose.x+x*pose.axis.x+z*pose.normal.x,wz=pose.z+x*pose.axis.z+z*pose.normal.z;
        return supports.some(p=>{const dx=wx-p.x,dz=wz-p.z;
            return Math.abs(dx*p.axis.x+dz*p.axis.z)<=p.width/2+.001
                &&Math.abs(dx*p.normal.x+dz*p.normal.z)<=p.depth/2+.001;});
    };
    const edges=[{length:pose.width,at:t=>[t,-pose.depth/2],out:[0,-1],angle:0},
        {length:pose.width,at:t=>[-t,pose.depth/2],out:[0,1],angle:Math.PI},
        {length:pose.depth,at:t=>[-pose.width/2,-t],out:[-1,0],angle:Math.PI/2},
        {length:pose.depth,at:t=>[pose.width/2,t],out:[1,0],angle:-Math.PI/2}];
    for(const edge of edges){
        const count=Math.max(2,Math.ceil(edge.length/.32)),spacing=edge.length/count;
        for(let i=0;i<count;i++){
            const t=-edge.length/2+(i+.5)*spacing,[x,z]=edge.at(t),[dx,dz]=edge.out;
            if(!supported(x+dx*.05,z+dz*.05)||!supported(x+dx*.15,z+dz*.15))continue;
            if(random()<.24)continue;
            const length=Math.min(.12+random()*.50,Math.min(pose.width,pose.depth)*.30);
            fragments.push({x,z,angle:edge.angle,width:spacing*(.7+.25*random()),length,
                drop:.04+random()*.18,splinter:random(),joist:i%5===2});
        }
    }
    return fragments;
}

function extruded(THREE,points,thickness) {
    const shape=new THREE.Shape();shape.moveTo(...points[0]);
    for(const point of points.slice(1))shape.lineTo(...point);
    shape.closePath();
    const geometry=new THREE.ExtrudeGeometry(shape,{depth:thickness,steps:1,bevelEnabled:false});
    // Тот же порядок атрибутов, что у цилиндров арматуры: существующее
    // объединение частей сравнивает их перед сборкой общего буфера.
    const uv=geometry.getAttribute('uv');geometry.deleteAttribute('uv');geometry.setAttribute('uv',uv);
    // Единый индекс позволяет существующему объединению частей собрать
    // обломки в один вызов отрисовки и один силуэт для карты теней.
    geometry.setIndex(Array.from({length:geometry.attributes.position.count},(_,i)=>i));
    return geometry;
}

export function createBrokenWallGeometry(THREE,width,height,depth,seed) {
    return extruded(THREE,brokenWallOutline(width,height,seed),depth).translate(0,0,-depth/2);
}

function plankGeometry(THREE,width,length,thickness,splinter) {
    const half=width/2;
    const points=[[-half,-.045],[half,-.045],[half,length*(.76+.20*splinter)],
        [half*.2,length*.87],[-half*.12,length*(.62+.12*splinter)],[-half,length]];
    return extruded(THREE,points,thickness).rotateX(Math.PI/2);
}

function fractureColors(THREE,geometry,kind) {
    const normals=geometry.attributes.normal,colors=[];
    for(let i=0;i<normals.count;i++){
        const cut=Math.abs(normals.getY(i))<.7;
        colors.push(cut?(kind==='wood'?1.2:.68):1,
            cut?(kind==='wood'?1.12:.65):1,cut?(kind==='wood'?.94:.59):1);
    }
    geometry.setAttribute('color',new THREE.Float32BufferAttribute(colors,3));
}

export function createOpeningFractures(THREE,fragments,material,kind='wood') {
    const group=new THREE.Group();
    material.vertexColors=true;
    for(const f of fragments){
        const mesh=new THREE.Mesh(plankGeometry(THREE,f.width,f.length,kind==='wood'?.025:.07,f.splinter),material);
        // Начало остаётся у уцелевшего края, свободный конец свисает в провал.
        const position=mesh.geometry.attributes.position;
        for(let i=0;i<position.count;i++){
            const fraction=Math.max(0,position.getZ(i))/f.length;
            position.setY(i,position.getY(i)-f.drop*fraction);
        }
        mesh.geometry.computeVertexNormals();mesh.position.set(f.x,.012,f.z);mesh.rotation.y=f.angle;group.add(mesh);
        fractureColors(THREE,mesh.geometry,kind);
        if(f.joist){
            const joist=new THREE.Mesh(plankGeometry(THREE,.13,Math.max(.25,f.length),.14,1-f.splinter),material);
            const jp=joist.geometry.attributes.position;
            for(let i=0;i<jp.count;i++)jp.setY(i,jp.getY(i)-Math.max(0,jp.getZ(i))*.7);
            joist.geometry.computeVertexNormals();fractureColors(THREE,joist.geometry,kind);
            joist.rotation.y=f.angle;joist.position.set(f.x,-.08,f.z);group.add(joist);
        }
    }
    return group;
}

export function createWallDebris(THREE,dimensions,material,kind,seed) {
    const group=new THREE.Group(),random=randomFor(seed),{width,depth,height}=dimensions;
    material.vertexColors=true;
    for(let i=0;i<9;i++){
        const w=width*(i===0?.62:i===1?.35:.09+random()*.09),d=depth*(i<2?.55:.16+random()*.16);
        const points=[[-w/2,-d/2],[w*.38,-d*.43],[w/2,d*.13],[w*.15,d/2],[-w*.39,d*.36]];
        const thickness=Math.min(height*.35,kind==='wood'?.035:.07);
        const geometry=extruded(THREE,points,thickness).rotateX(Math.PI/2);
        fractureColors(THREE,geometry,kind);
        const mesh=new THREE.Mesh(geometry,material);
        mesh.position.set((random()-.5)*(width-w)*.85,thickness+random()*(height-thickness)*.65,
            (random()-.5)*(depth-d)*.75);
        mesh.rotation.set((random()-.5)*.16,(random()-.5)*.65,(random()-.5)*.18);
        group.add(mesh);
    }
    if(kind!=='wood'){
        const points=[[-width*.2,.06,-depth*.12],[width*.08,height*.72,depth*.06],[width*.22,height*.53,depth*.12]];
        for(let i=1;i<points.length;i++){
            const a=new THREE.Vector3(...points[i-1]),b=new THREE.Vector3(...points[i]),delta=b.clone().sub(a);
            const geometry=new THREE.CylinderGeometry(.009,.009,delta.length(),6);
            const colors=Array.from({length:geometry.attributes.position.count},()=>[.23,.22,.19]).flat();
            geometry.setAttribute('color',new THREE.Float32BufferAttribute(colors,3));
            const rod=new THREE.Mesh(geometry,material);rod.position.copy(a.add(b).multiplyScalar(.5));
            rod.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),delta.normalize());group.add(rod);
        }
    }
    return group;
}
