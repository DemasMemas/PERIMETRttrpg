// Ordinary procedural building parts, using the editor's existing materials.
export function createVerticalAccessModel(THREE, object, {width,depth,height}, material) {
    const root = new THREE.Group();
    const metal = object.type === 'floor_opening' ? material
        : new THREE.MeshStandardMaterial({color:'#55594f',metalness:.7,roughness:.55});
    const box = (parent,w,h,d,x,y,z,mat=material) => {
        const mesh=new THREE.Mesh(new THREE.BoxGeometry(w,h,d),mat);
        mesh.position.set(x,y,z);parent.add(mesh);return mesh;
    };
    if (object.type === 'ladder') {
        const rail = Math.min(.07,width*.09), span=width-rail*2;
        for (const side of [-1,1]) {
            box(root,rail,height,rail,side*span/2,height/2,depth*.28);
            for (const y of [.15,height-.15]) box(root,rail*1.5,.05,depth*.35,side*span/2,y,depth*.4,metal);
        }
        const count=Math.max(2,Math.floor(height/.28));
        for (let i=1;i<=count;i++) {
            const rung=new THREE.Mesh(new THREE.CylinderGeometry(.025,.025,span,10),material);
            rung.rotation.z=Math.PI/2;rung.position.set(0,height*i/(count+1),depth*.28);root.add(rung);
        }
        return root;
    }
    if (object.type === 'floor_opening') {
        // Проём — отсутствие перекрытия. Рамки соседних проёмов создавали
        // ложные балки над пустотой и выступали за внешний контур здания.
        // Невидимая поверхность сохраняет выбор проёма в редакторе и меню.
        const pick = new THREE.Mesh(new THREE.PlaneGeometry(width, depth),
            new THREE.MeshBasicMaterial({visible:false, colorWrite:false, depthWrite:false, side:THREE.DoubleSide}));
        pick.rotation.x = -Math.PI / 2;
        pick.userData.pickOnly=true;
        root.add(pick);
        return root;
    }
    const sealed=object.properties?.visual_asset === 'bunker_hatch';
    const rim=Math.min(width,depth)*.075;
    for (const sign of [-1,1]) {
        box(root,width+rim*2,.045,rim,0,.015,sign*(depth+rim)/2,metal);
        box(root,rim,.045,depth,sign*(width+rim)/2,.015,0,metal);
    }
    const lid=new THREE.Group();lid.position.set(0,.035,-depth/2);root.add(lid);
    if (sealed) {
        box(lid,width,height,depth,0,-height/2,depth/2);
        for (const x of [-width*.3,width*.3]) box(lid,.045,.035,depth*.8,x,.02,depth/2,metal);
        const wheel=new THREE.Mesh(new THREE.TorusGeometry(Math.min(width,depth)*.18,.025,8,24),metal);
        wheel.rotation.x=Math.PI/2;wheel.position.set(0,.09,depth/2);lid.add(wheel);
        box(lid,width*.36,.035,.025,0,.09,depth/2,metal);
        box(lid,.025,.035,depth*.36,0,.09,depth/2,metal);
    } else {
        const count=5;
        for (let i=0;i<count;i++) box(lid,width/count-.006,height,depth,-width/2+(i+.5)*width/count,-height/2,depth/2);
        for (const z of [depth*.2,depth*.8]) box(lid,width*.9,.025,.055,0,.015,z,metal);
        const ring=new THREE.Mesh(new THREE.TorusGeometry(.065,.012,6,16),metal);
        ring.position.set(0,.055,depth*.78);lid.add(ring);
    }
    lid.rotation.x=object.properties?.is_open ? -Math.PI*.48 : 0;
    root.userData.hatchLid=lid;
    return root;
}
