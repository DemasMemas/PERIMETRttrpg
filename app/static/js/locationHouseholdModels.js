// Original project models. One shared geometry per ID; one atlas for the family.
// Local scene lighting, selection and previews tint materials individually.
const caches = new WeakMap();
const wood='#77604a', edge='#463e33', cloth='#77715c', iron='#444a44', enamel='#b2b2a0', dark='#272d29';
const ceramicModels = new Set(['washbasin', 'toilet', 'bathtub']);
const glassModels=new Set(['display_case','refrigerated_display']);
const smoothModels=new Set(['tv_crt','tv_flat','tv_wall','washing_machine','gas_boiler','water_heater','radiator','towel_rail','mirror',
    'switchboard','plate','glass','mug','bowl','pot','cauldron','pan','kettle','fork','spoon','knife','bucket_metal','bucket_plastic','basin',
    'dish_rack','cash_register','shop_scales','bottle','tin_can','shower','road_sign','dumpster','car_sedan','car_van','car_burned',
    'aeration_column']);

// Keep reusable CPU geometry and atlas pixels, release the departed renderer's
// GPU allocations and its dispose subscriptions on these cached resources.
export function releaseHouseholdRendererResources(T) {
    const cache = caches.get(T);
    if (!cache) return;
    cache.texture.dispose();
    cache.geometries.forEach(geometry => geometry.dispose());
}

function resources(T) {
    if(caches.has(T))return caches.get(T);
    const canvas=document.createElement('canvas');canvas.width=canvas.height=256;
    const ctx=canvas.getContext('2d'),pixels=ctx.createImageData(256,256);
    let seed=2403;const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296};
    for(let y=0;y<256;y++)for(let x=0;x<256;x++){
        const tile=(x>>7)+2*(y>>7),u=x%128,v=y%128;
        let value=226+random()*20;
        if(tile===0)value-=12*Math.sin(v*.8+Math.sin(u*.035)*2)**8;
        if(tile===1)value-=(u%4===0?13:0)+(v%4===0?13:0);
        if(tile===2)value-=random()<.015?40:0;
        if(tile===3)value-=v%19===0?6:0;
        const i=(y*256+x)*4;pixels.data.set([value,value,value,255],i);
    }
    ctx.putImageData(pixels,0,0);
    const texture=new T.CanvasTexture(canvas);texture.encoding=T.sRGBEncoding;texture.anisotropy=2;
    const material=new T.MeshStandardMaterial({map:texture,vertexColors:true,color:0xffffff,roughness:.9,metalness:.03});
    const cache={texture,material,geometries:new Map()};caches.set(T,cache);return cache;
}

function geometryFor(T,id,cache) {
    if(cache.geometries.has(id))return cache.geometries.get(id);
    const ceramic=ceramicModels.has(id);
    const positions=[],normals=[],colors=[],uvs=[],sinkParts=[[],[]];
    const trunkRanges=[];
    const markTrunk=build=>{const start=positions.length/3;build();trunkRanges.push([start,positions.length/3]);};
    let crateBottomStart = -1;
    const add=(geo,x,y,z,sx,sy,sz,color,tile=0,rx=0,rz=0,ry=0,groundY=null)=>{
        const flat=geo.index?geo.toNonIndexed():geo;
        const m=new T.Matrix4().compose(new T.Vector3(x,y,z),new T.Quaternion().setFromEuler(new T.Euler(rx,ry,rz)),new T.Vector3(sx,sy,sz));
        const glazed=ceramic||(id==='sink'&&tile===2);
        const surfaceColor=glazed
            ? (color===enamel?'#d2d0c4':color==='#aeb1a1'?'#cecec0':color==='#bcbba5'?'#d0c9b2':color)
            : color;
        flat.applyMatrix4(m);
        if(groundY!==null){flat.computeBoundingBox();flat.translate(0,groundY-flat.boundingBox.min.y,0);}
        const p=flat.attributes.position,n=flat.attributes.normal,uv=flat.attributes.uv,c=new T.Color(surfaceColor);
        for(let i=0;i<p.count;i++){
            if(id==='sink'||glassModels.has(id))sinkParts[(id==='sink'?glazed:color==='#9ab9b4')?1:0].push(positions.length/3);
            positions.push(p.getX(i),p.getY(i),p.getZ(i));normals.push(n.getX(i),n.getY(i),n.getZ(i));
            // Glaze and flat rug prints need faint ageing, not triangle patches.
            const wear=glazed||id==='floor_rug'
                ? .985+.012*Math.sin(p.getX(i)*3.1+p.getY(i)*2.3)*Math.cos(p.getZ(i)*2.7)
                : .96+.04*Math.sin(Math.floor(i/3)*2.7+positions.length*.13);
            colors.push(c.r*wear,c.g*wear,c.b*wear);
            uvs.push((tile%2+.02+uv.getX(i)*.96)/2,(Math.floor(tile/2)+.02+uv.getY(i)*.96)/2);
        }
        flat.dispose();if(flat!==geo)geo.dispose();
    };
    const B=(x,y,z,w,h,d,c=wood,t=0,rx=0,rz=0)=>add(new T.BoxGeometry(1,1,1),x,y,z,w,h,d,c,t,rx,rz);
    const C=(x,y,z,r,h,c=iron,t=2,rx=0,rz=0,top=1)=>add(new T.CylinderGeometry(top,1,1,10),x,y,z,r,h,r,c,t,rx,rz);
    const legs=(top=.9,span=.4)=>{for(const x of [-span,span])for(const z of [-span,span])B(x,top/2,z,.065,top,.07,edge)};
    const knob=(x,y,z)=>B(x,y,z,.04,.025,.045,'#a29a79',2);
    // A hollow enamel shell: rounded outer wall, rim and recessed inner bowl.
    // All parts still join the family's single mesh/material.
    const bowl=(rings,z=0,color=enamel)=>{
        const vertices=[],indices=[],texcoords=[],steps=5,ringSize=4*steps;
        for(const [y,w,d,r]of rings)for(let corner=0;corner<4;corner++)for(let i=0;i<steps;i++){
            const angle=(corner+i/(steps-1))*Math.PI/2;
            const x=(corner===0||corner===3?1:-1)*(w/2-r)+Math.cos(angle)*r;
            const depth=(corner<2?1:-1)*(d/2-r)+Math.sin(angle)*r;
            vertices.push(x,y,depth);texcoords.push(x+.5,depth+.5);
        }
        for(let ring=0;ring<rings.length-1;ring++)for(let i=0;i<ringSize;i++){
            const a=ring*ringSize+i,b=ring*ringSize+(i+1)%ringSize,c=a+ringSize,d=b+ringSize;
            indices.push(a,c,b,b,c,d);
        }
        const shell=new T.BufferGeometry();shell.setAttribute('position',new T.Float32BufferAttribute(vertices,3));
        shell.setAttribute('uv',new T.Float32BufferAttribute(texcoords,2));shell.setIndex(indices);shell.computeVertexNormals();
        add(shell,0,0,z,1,1,1,color,2);
    };
    const cabinet=(drawers=0)=>{
        B(0,.52,0,.96,.9,.92);B(0,.98,0,1,.04,1,id==='kitchen_cabinet'?'#a7a68e':edge,id==='kitchen_cabinet'?2:0);B(0,.08,0,.88,.1,.84,edge);
        if(drawers){for(let i=0;i<drawers;i++){const y=.22+i*.72/drawers;B(0,y,.475,.88,.65/drawers,.035,'#8b7050');knob(0,y,.51)}}
        else for(const x of [-.235,.235]){B(x,.54,.475,.45,.79,.035,'#89704e');knob(x<0?-.04:.04,.55,.51)}
    };
    if(buildAdditionalHouseholdModel(T,id,{B,C,add,legs,markTrunk})) {
        // Additional models use the same merged geometry, atlas and lifecycle.
    }else if(id==='bed_single'||id==='bed_double'){
        for(const x of [-.43,.43])for(const z of [-.43,.43])B(x,.18,z,.07,.36,.06,iron,2);
        B(0,.38,0,.96,.16,.94,edge);B(0,.55,0,.92,.18,.87,'#a29b82',1);
        B(0,.58,.15,.93,.16,.55,'#65695b',1);B(0,.69,-.31,.65,.12,.17,'#b0aa94',1);
        // Panels fit between the posts, avoiding overlapping front/top faces.
        for(const z of [-.48,.48]){B(0,z<0?.82:.55,z,.86,z<0?.36:.2,.04,wood);for(const x of [-.46,.46])B(x,z<0?.57:.35,z,.05,z<0?.86:.56,.05,edge)}
        for(const x of [-.32,0,.32])B(x,.667,.15,.012,.008,.55,'#777a66',1);
    }else if(['nightstand','wardrobe','dresser','kitchen_cabinet'].includes(id)){
        cabinet(id==='dresser'?4:id==='nightstand'?2:0);
        // cabinet() already provides the top; a second cap shared its sides.
        if(id==='wardrobe'){for(const x of [-.38,.38])B(x,.51,.498,.018,.66,.012,edge)}
    }else if(['dining_table','stool','wood_chair'].includes(id)){
        const seat=id==='wood_chair'?.5:.94;legs(seat,.39);B(0,seat,0,1,.08,1,'#8b7253');
        for(const z of [-.38,.38])B(0,seat-.13,z,.8,.12,.05,edge);
        if(id==='wood_chair'){for(const x of [-.4,.4])B(x,.75,-.41,.07,.5,.07,edge);B(0,.88,-.41,.87,.17,.08);B(0,.66,-.41,.8,.09,.065)}
        if(id==='stool')for(const z of [-.39,.39])B(0,.3,z,.8,.06,.06,edge);
    }else if(id==='armchair'){
        legs(.18,.37);B(0,.35,.06,.82,.3,.8,cloth,1);B(0,.7,-.35,.91,.6,.24,'#686553',1,-.12);
        for(const x of [-.43,.43]){B(x,.49,.02,.14,.44,.9,'#5d604f',1);B(x,.72,.03,.15,.04,.88,wood)}
        B(0,.53,.08,.72,.07,.69,'#85816c',1);B(0,.73,-.21,.018,.34,.01,edge,1);
    }else if(id==='wall_shelf'){
        B(0,.92,0,1,.16,1);for(const x of [-.36,.36]){B(x,.44,-.43,.07,.88,.08,iron,2);B(x,.6,-.05,.07,.06,.9,iron,2,.5)}
        B(0,.85,-.46,1,.3,.07,edge);
    }else if(id==='storage_rack'){
        for(const x of [-.46,.46])for(const z of [-.43,.43])B(x,.5,z,.045,1,.055,iron,2);
        for(const y of [.06,.36,.66,.96]){B(0,y,0,.94,.035,.96,'#8b795a');B(0,y-.03,.46,.94,.06,.035,iron,2)}
        B(0,.5,-.45,.04,1,.03,edge,0,0,.45);
    }else if(id==='floor_rug'||id==='wall_rug'){
        const wall=id==='wall_rug';let layer=0;
        const panel=(x,z,w,d,c)=>{const offset=layer++*.24;wall?B(x,z+.5,-.3+offset,w,d,.2,c,1):B(x,.12+offset,z,w,.2,d,c,1)};
        panel(0,0,1,1,'#654944');panel(0,0,.9,.9,'#8b7556');panel(0,0,.83,.83,'#574e45');
        for(const z of [-.31,0,.31])for(const x of [-.26,0,.26]){
            if(wall)B(x,z+.5,.43,.15,.15,.15,'#9d8765',1,0,Math.PI/4);
            else add(new T.BoxGeometry(1,1,1),x,.88,z,.15,.12,.15,'#9d8765',1,0,0,Math.PI/4);
        }
    }else if(id==='refrigerator'){
        B(0,.5,0,.96,1,.9,enamel,2);for(const [y,h]of [[.26,.47],[.76,.47]]){B(0,y,.475,.93,h,.05,'#c1bfab',2);B(-.34,y+.02,.52,.035,.19,.035,iron,2)}
        B(0,.51,.509,.94,.015,.007,edge,2);B(.3,.04,.51,.1,.05,.01,'#7c6c4f',2);
    }else if(id==='cooker'){
        B(0,.48,0,.95,.92,.96,enamel,2);B(0,.98,0,1,.04,1,iron,2);B(0,.42,.49,.79,.51,.035,iron,2);
        B(0,.42,.515,.62,.33,.02,'#383f3a',2);B(0,.71,.53,.69,.035,.055,'#8d9187',2);
        for(const x of [-.26,.26])for(const z of [-.26,.26]){C(x,1,z,.17,.012,dark);B(x,1.009,z,.42,.012,.026,iron,2);B(x,1.009,z,.026,.012,.42,iron,2)}
        for(const x of [-.3,-.1,.1,.3])C(x,.85,.51,.035,.04,dark,2,Math.PI/2);
    }else if(id==='sink'){
        // Open cabinet carcass and a countertop with a real bowl opening.
        // A solid cabinet block/top would fill the recessed basin again.
        for(const x of [-.46,.46])B(x,.52,0,.06,.9,.92);
        B(0,.52,-.445,.86,.9,.03);B(0,.08,0,.88,.1,.84,edge);
        for(const x of [-.235,.235]){B(x,.54,.475,.45,.79,.035,'#89704e');knob(x<0?-.04:.04,.55,.51)}
        for(const z of [-.4125,.4125])B(0,.98,z,1,.04,.175,enamel,2);
        for(const x of [-.405,.405])B(x,.98,0,.19,.04,.65,enamel,2);
        bowl([[.81,.42,.45,.12],[.99,.64,.67,.13],[1.01,.64,.67,.13],[1.01,.55,.58,.1],[.84,.36,.39,.1],[.83,.01,.01,.004]],0,'#b2b8ae');
        C(0,.845,0,.035,.008,dark);
        B(0,1.11,-.36,.035,.25,.035,iron,2);B(0,1.23,-.22,.035,.035,.3,iron,2);B(.1,1.02,-.36,.14,.035,.04,iron,2);
    }else if(id==='washbasin'){
        B(0,.025,-.12,.35,.05,.35,'#939485',2);C(0,.33,-.12,.12,.62,enamel);
        bowl([[.63,.62,.54,.16],[.82,1,.86,.2],[.84,1,.86,.2],[.84,.83,.69,.16],[.69,.42,.32,.12],[.68,.01,.01,.004]],.04);
        B(0,.8,-.4,.94,.1,.12,enamel,2);C(0,.9,-.39,.025,.17,iron);
        B(0,.98,-.25,.05,.04,.3,'#8e968f',2);C(0,.945,-.11,.024,.05,iron);
        for(const x of [-.22,.22]){C(x,.88,-.39,.034,.08,'#929a92');B(x,.925,-.39,.12,.025,.035,iron,2)}
        C(0,.691,.04,.04,.006,dark);
    }else if(id==='toilet'){
        B(0,.025,.08,.55,.05,.58,'#9d9e8d',2);C(0,.23,.08,.22,.4,enamel,2,0,0,.8);
        bowl([[.3,.46,.48,.22],[.53,.94,.76,.32],[.57,.94,.76,.32],[.57,.75,.6,.27],[.37,.34,.3,.14],[.36,.01,.01,.004]],.12);
        bowl([[.57,.96,.78,.33],[.61,.96,.78,.33],[.61,.76,.6,.27],[.57,.76,.6,.27]],.12,'#bcbba5');
        // Keep the cistern above the seat: its former front face at z=-.18
        // coincided with the seat's inner rear wall (y=.57..61).
        B(0,.79,-.32,.83,.33,.28,enamel,2);B(0,.975,-.32,.88,.045,.32,'#c1c0ad',2);
        for(const x of [-.24,.24])B(x,.61,-.23,.075,.05,.07,iron,2);
        B(.27,.83,-.162,.12,.035,.035,iron,2);C(0,.367,.12,.08,.008,'#65756b');
        for(const x of [-.23,.23])C(x,.055,.12,.025,.02,iron);
    }else if(id==='bathtub'){
        for(const x of [-.33,.33])for(const z of [-.32,.32])B(x,.065,z,.09,.13,.08,iron,2);
        bowl([[.12,.64,.76,.15],[.9,.96,.98,.18],[.95,1,1,.18],[.95,.87,.9,.15],[.3,.57,.68,.14],[.29,.01,.01,.004]],0,'#aeb1a1');
        C(0,.305,-.25,.04,.009,'#747f75');
        // Mount both feet inside the rear rim's z=-.50..-.45 band.
        // The mixer, riser and spout overlap rather than floating apart.
        for(const x of [-.18,.18])C(x,.94,-.475,.025,.06,iron);
        B(0,.97,-.475,.46,.035,.04,iron,2);B(0,1.01,-.475,.035,.1,.035,iron,2);
        B(0,1.05,-.39,.04,.03,.2,'#929a92',2);
    }else if(id==='wood_stove'){
        B(0,.23,0,1,.44,1,'#8d8270',2);B(0,.48,0,1,.06,1,iron,2);B(0,.25,.515,.57,.23,.035,iron,2);knob(.2,.25,.55);
        B(0,.08,.52,.4,.05,.02,dark,2);C(0,.515,0,.28,.014,dark);C(-.28,.75,-.25,.1,.5,iron);
        for(const y of [.55,.9])C(-.28,y,-.25,.116,.025,'#696a5d');B(.29,.25,.52,.015,.16,.01,'#6b6252',2);
    }else if(id==='generator'){
        for(const x of [-.45,.45])for(const z of [-.42,.42]){B(x,.45,z,.045,.84,.045,iron,2);B(0,.08,z,.94,.06,.045,iron,2);B(0,.9,z,.94,.055,.045,iron,2)}
        B(0,.76,0,.78,.2,.77,'#686f4e',2);C(0,.88,-.12,.1,.04,dark);C(-.16,.4,0,.25,.58,'#706e60',2,0,Math.PI/2);
        B(.26,.42,0,.33,.4,.58,dark,2);for(const y of [.27,.35,.43,.51])B(.435,y,0,.012,.025,.48,'#8a8b7a',2);
        B(0,.42,.44,.48,.26,.035,'#7c7d68',2);for(const x of [-.13,.13])C(x,.42,.47,.06,.02,dark,2,Math.PI/2);
    }else if(id==='old_computer'){
        B(-.15,.62,-.12,.58,.61,.67,'#a3a18d',2);B(-.15,.32,-.1,.44,.1,.45,'#817e6e',2);
        B(-.15,.65,.23,.49,.45,.04,edge,2);B(-.15,.65,.255,.42,.37,.015,'#3f524b',2);
        B(.32,.49,-.04,.31,.96,.78,'#969782',2);B(.32,.75,.36,.24,.065,.025,edge,2);
        for(const y of [.38,.44,.5,.56])B(.32,y,.36,.22,.012,.02,'#5d6659',2);knob(.4,.62,.37);
        B(-.15,.05,.35,.62,.09,.28,'#aba58e',2);
        for(let row=0;row<4;row++)for(let col=0;col<9;col++)B(-.41+col*.064,.103,.25+row*.06,.048,.025,.045,'#767969',2);
    }else if(id==='desk_lamp'){
        C(0,.07,0,.4,.14,'#59644b');B(0,.45,-.12,.055,.72,.055,iron,2,0,-.16);C(.04,.83,0,.32,.3,'#59644b',2,.25,0,.5);
        C(.04,.7,.03,.27,.01,'#b1aa89',2,.25);
    }else if(id==='wood_crate'){
        for(let i=0;i<4;i++){const y=.14+i*.23;for(const z of [-.46,.46])B(0,y,z,1,.19,.065,i%2?wood:'#897557');for(const x of [-.46,.46])B(x,y,0,.065,.19,.87,wood)}
        for(const x of [-.4,.4])for(const z of [-.43,.43])B(x,.5,z,.075,.94,.075,edge);
        crateBottomStart=positions.length/3;
        for(let i=0;i<5;i++)B(-.4+i*.2,.04,0,.18,.08,.91,wood);
    }else if(id==='cardboard_box'){
        B(0,.49,0,.98,.98,.98,'#96805a',3);B(0,.987,0,.04,.012,.97,edge,3);B(0,.997,0,.17,.006,1,'#b3a17b',3);
        B(0,.47,.496,.49,.26,.012,'#b5a789',3);for(const x of [-.12,-.06,0,.06,.12])B(x,.47,.504,.015,.16,.004,edge,3);
    }else if(id==='jerrycan'){
        B(0,.43,0,.94,.83,.94,'#626e4f',2);B(0,.87,0,.91,.07,.87,'#5b664a',2);
        B(-.12,.98,0,.56,.04,.18,iron,2);for(const x of [-.37,.13])B(x,.92,0,.06,.12,.18,iron,2);C(.34,.94,0,.11,.1,iron);
        for(const z of [-.48,.48])for(const sign of [-1,1])B(0,.44,z,.045,.65,.025,'#7d8568',2,0,sign*.65);
    }else throw Error('Unknown household model: '+id);
    const geo=new T.BufferGeometry();geo.setAttribute('position',new T.Float32BufferAttribute(positions,3));
    geo.setAttribute('normal',new T.Float32BufferAttribute(normals,3));geo.setAttribute('color',new T.Float32BufferAttribute(colors,3));geo.setAttribute('uv',new T.Float32BufferAttribute(uvs,2));
    if(id==='sink'||glassModels.has(id)){
        // Two batches share the same geometry: wood retains its atlas; the
        // basin/countertop use smooth enamel without the furniture texture.
        geo.setIndex(sinkParts[0].concat(sinkParts[1]));
        geo.addGroup(0,sinkParts[0].length,0);geo.addGroup(sinkParts[0].length,sinkParts[1].length,1);
    }
    // Keep exact physical catalogue bounds, including details such as taps/feet.
    geo.computeBoundingBox();const box=geo.boundingBox,size=box.getSize(new T.Vector3()),center=box.getCenter(new T.Vector3());
    geo.translate(-center.x,-box.min.y,-center.z);geo.scale(1/size.x,1/size.y,1/size.z);
    if(trunkRanges.length){
        // Контур движения относится к стволу, а не к общему объёму кроны.
        const trunk=new T.Box3(),p=geo.attributes.position;
        for(const [start,end]of trunkRanges)for(let i=start;i<end;i++)trunk.expandByPoint(new T.Vector3().fromBufferAttribute(p,i));
        geo.userData.movementTrunk={min:trunk.min.toArray(),max:trunk.max.toArray()};
    }
    if(crateBottomStart>=0){
        // Apply after normalisation, otherwise re-centring would cancel the gap.
        // One centimetre at the catalogue's original 0.42 m height; pivot stays put.
        const p=geo.attributes.position;
        for(let i=crateBottomStart;i<p.count;i++)p.setY(i,p.getY(i)+.01/.42);
        geo.computeBoundingBox();
    }
    geo.userData.householdShared=true;cache.geometries.set(id,geo);return geo;
}

export function createHouseholdModel(T,asset,dimensions,color) {
    const cache=resources(T),id=asset.visual?.modelId||asset.id.replace('household_','');
    const material=cache.material.clone();
    if(ceramicModels.has(id)){
        // The furniture atlas carries directional wood/cloth patterns. Glaze
        // uses vertex colours only, with no fibre texture, normal or bump map.
        material.map=null;material.roughness=.28;material.metalness=0;
    }
    if(smoothModels.has(id)){material.map=null;material.roughness=.5;material.metalness=.12;}
    if(/^(oak|birch|pine|bush|tall_grass)_/.test(id)){material.map=null;material.metalness=0;material.roughness=1;material.side=T.DoubleSide;}
    if(color&&color.toLowerCase()!==asset.color.toLowerCase())material.color.set(color);
    let materials=material;
    if(id==='sink'){
        const glaze=material.clone();glaze.map=null;glaze.roughness=.28;glaze.metalness=0;
        materials=[material,glaze];
    }
    if(glassModels.has(id)){
        const glass=material.clone();glass.map=null;glass.transparent=true;glass.opacity=.22;glass.depthWrite=false;glass.roughness=.15;
        materials=[material,glass];
    }
    const mesh=new T.Mesh(geometryFor(T,id,cache),materials);
    mesh.scale.set(dimensions.width,dimensions.height,dimensions.depth);
    mesh.userData.householdAssetId=asset.id;return mesh;
}

function buildAdditionalHouseholdModel(T,id,{B,C,add,legs,markTrunk}) {
    const grey='#85877c',rust='#786052',rubber='#30362f',white='#c7c5b1',blue='#627d7c';
    const ring=(x,y,z,r,t,c=iron,rx=Math.PI/2)=>add(new T.TorusGeometry(r,t,5,18),x,y,z,1,1,1,c,2,rx);
    const ball=(x,y,z,w,h,d,c)=>add(new T.SphereGeometry(1,12,8),x,y,z,w,h,d,c,2);
    const rod=(a,b,r=.018,c=iron)=>{
        const start=new T.Vector3(...a),end=new T.Vector3(...b),delta=end.clone().sub(start);
        const e=new T.Euler().setFromQuaternion(new T.Quaternion().setFromUnitVectors(new T.Vector3(0,1,0),delta.clone().normalize()));
        const p=start.add(end).multiplyScalar(.5);
        add(new T.CylinderGeometry(1,1,1,6),p.x,p.y,p.z,r,delta.length(),r,c,2,e.x,e.z,e.y);
    };
    const vessel=(r=.4,h=.7,c=white)=>{
        // Open outer/inner walls, rim and recessed base, never a solid cylinder.
        add(new T.CylinderGeometry(r,r*.78,h,18,1,true),0,h/2,0,1,1,1,c,2);
        add(new T.CylinderGeometry(r-.035,r*.78-.035,h-.045,18,1,true).scale(-1,1,1),0,h/2+.02,0,1,1,1,c,2);
        C(0,.025,0,r*.78,.05,c);ring(0,h,0,r-.015,.025,c);
    };
    const handle=(side,y=.6)=>ring(side,y,0,.13,.025,iron,0);
    const screen=(x,y,z,w,h)=>{B(x,y,z,w,h,.035,dark,2);B(x,y,z+.022,w*.9,h*.85,.008,'#4b6460',2);};
    const slats=(y,w=1,d=.8,n=5)=>{for(let i=0;i<n;i++)B(0,y,-d/2+(i+.5)*d/n,w,.055,d/n*.85,wood);};
    const stone=(x,y,z,sx,sy,sz,c=grey,angle=0)=>add(new T.DodecahedronGeometry(1,0),x,y,z,sx,sy,sz,c,2,.2,angle,angle*.7,0);
    if(id.startsWith('trap_')){
        const kind=id.slice(5);
        if(kind==='mine'){C(0,.08,0,.42,.16,'#4d5945',2);C(0,.19,0,.15,.08,iron,2);}
        else if(kind==='beartrap'){
            B(0,.035,0,.9,.07,.65,iron,2);C(0,.09,0,.13,.04,rust,2);
            for(const side of [-1,1])for(let i=0;i<7;i++)B(-.4+i*.13,.14,side*.28,.06,.2,.04,grey,2,side*.4);
        }else if(kind==='net'){
            for(let i=0;i<8;i++){const p=-.45+i*.13;rod([p,.02,-.45],[p,.025,.45],.012,cloth);rod([-.45,.02,p],[.45,.025,p],.012,cloth);}
        }else if(kind==='tripwire'||kind==='alarm'){
            for(const x of [-.45,.45])C(x,.15,0,.025,.3,iron,2);
            rod([-.45,.12,0],[.45,.12,0],.006,dark);C(-.3,.08,.16,.1,.16,kind==='alarm'?grey:'#596044',2);
        }else{
            for(let i=0;i<5;i++){const x=Math.sin(i*4)*.35,z=Math.cos(i*3)*.35;
                rod([x-.08,.01,z-.08],[x+.08,.2,z+.08],.018,iron);rod([x+.1,.01,z-.06],[x-.06,.16,z+.1],.018,iron);}
        }
        return true;
    }else if(id==='curtain_panel'){
        for(let i=0;i<24;i++)B(-.5+(i+.5)/24,.5,Math.sin(i*Math.PI/3)*.32,1/24+.002,1,.18,cloth,1);
        C(0,.99,0,.01,.02,iron);
    }else if(id==='barricade_wood'){
        for(const x of [-.37,.37])B(x,.48,-.32,.1,.96,.24,edge);
        for(let i=0;i<5;i++)B(0,.1+i*.195,.08+(i%2)*.04,1,.18,.3,i%2?wood:'#695641');
    }else if(id==='barricade_metal'||id==='barricade_reinforced'){
        B(0,.5,0,1,1,.16,rust,2);
        for(const x of [-.4,.4])B(x,.5,-.22,.075,1,.24,iron,2);
        if(id==='barricade_reinforced')for(const y of [.08,.5,.92])B(0,y,.2,1,.07,.3,iron,2);
        for(const x of [-.4,.4])for(const y of [.1,.9])ball(x,y,.12,.025,.025,.08,grey);
    }else if(id==='barricade_brick'){
        for(let row=0;row<9;row++)for(let column=-1;column<4;column++){
            const start=-.5+column*.25+(row%2?.125:0),left=Math.max(-.5,start),right=Math.min(.5,start+.24);
            if(right>left)B((left+right)/2,(row+.5)/9,0,right-left,.103,1,row%3?'#845e48':'#735342',2);
        }
    }else if(id==='aeration_column') {
        const tank='#1d7297',tankShade='#155779',steel='#a9aca8',brass='#a78e55',panel='#222a2c';
        C(0,.045,0,.35,.09,'#191d1d');
        C(0,.43,0,.30,.69,tank);C(0,.775,0,.29,.045,tankShade);
        ball(0,.79,0,.29,.035,.29,tankShade);
        C(0,.815,0,.085,.035,steel);C(0,.875,0,.035,.11,brass);
        rod([-.28,.2,.02],[-.28,.75,.02],.024,steel);
        rod([-.28,.75,.02],[-.16,.80,.02],.024,steel);
        C(-.28,.26,.02,.04,.08,brass);C(-.28,.61,.02,.04,.055,brass);
        B(-.31,.48,.20,.12,.14,.09,panel,2);
        B(-.31,.48,.255,.065,.07,.008,'#607f72',2);
        C(.12,.90,.04,.025,.15,brass);
        C(.12,.975,.055,.075,.02,steel,2,Math.PI/2);
        ring(.12,.975,.07,.073,.009,panel,0);
        rod([.12,.975,.075],[.15,1.01,.075],.005,panel);
        B(.44,.66,0,.23,.18,.21,panel,2);
        C(.31,.66,0,.065,.11,panel,2,0,Math.PI/2);
        rod([.26,.73,0],[.35,.78,0],.014,panel);
        return true;
    }
    if(['curtain_panel','barricade_wood','barricade_metal','barricade_reinforced','barricade_brick'].includes(id))return true;
    if(/^(oak|birch|pine|bush|tall_grass)_(green|dry|dead)$/.test(id)) {
        const [species,...rest]=id.split('_'),state=rest.at(-1),grass=id.startsWith('tall_grass');
        const dead=state==='dead',dry=state==='dry',bark=species==='birch'?'#b2b1a1':'#685c48';
        const leaf=dead?'#82765a':dry?'#8a8057':'#556a43';
        if(grass){
            for(let i=0;i<34;i++){
                const a=i*2.4,r=.42*Math.sqrt(i/34),x=Math.cos(a)*r,z=Math.sin(a)*r,h=.38+(i%7)*.095;
                const tip=[x+Math.sin(i*3)*.15,h,z+Math.cos(i*2)*.12];
                rod([x,0,z],tip,.008,leaf);
                for(const s of [-1,1])rod([x,h*.38,z],[tip[0]+s*.1,h*.72,tip[2]+s*.08],.006,leaf);
                if(i%3===0)for(let j=0;j<3;j++)ball(tip[0],h-j*.025,tip[2],.017,.035,.017,dry||dead?'#978465':'#7e855b');
            }
        }else{
            const shrub=species==='bush',birch=species==='birch',pine=species==='pine';
            const noise=i=>{let n=Math.imul(i+139,1597334677);n=Math.imul(n^(n>>>13),2246822519);return(n>>>0)/4294967296;};
            const branch=(a,b,r1,r2,sides=7)=>{
                const start=new T.Vector3(...a),end=new T.Vector3(...b),delta=end.clone().sub(start);
                const e=new T.Euler().setFromQuaternion(new T.Quaternion().setFromUnitVectors(new T.Vector3(0,1,0),delta.clone().normalize()));
                const c=start.add(end).multiplyScalar(.5);
                add(new T.CylinderGeometry(r2,r1,delta.length(),sides),c.x,c.y,c.z,1,1,1,bark,0,e.x,e.z,e.y);
            };
            const crown=(centre,index)=>{
                if(dead)return;
                const sprigs=pine?6:10,perSprig=pine?8:8;
                for(let sprig=0;sprig<sprigs;sprig++){
                    const seed=index*157+sprig,a=noise(seed)*Math.PI*2,u=noise(seed+39)*1.3-.3;
                    const spread=pine?.075:birch?.085:shrub?.105:.12;
                    const end=[centre[0]+Math.cos(a)*spread,centre[1]+u*spread*.6,centre[2]+Math.sin(a)*spread];
                    branch(centre,end,.0011,.0002,3);
                    for(let j=0;j<perSprig;j++){
                        const n=seed*19+j;if(dry&&noise(n)>.58)continue;
                        const t=(j+.5)/perSprig,side=j%2?1:-1;
                        const x=centre[0]+(end[0]-centre[0])*t+Math.sin(a)*side*.008;
                        const z=centre[2]+(end[2]-centre[2])*t-Math.cos(a)*side*.008;
                        const y=centre[1]+(end[1]-centre[1])*t;
                        const c=dry?(noise(n+13)>.55?'#81794e':'#6e704b'):(noise(n+13)>.5?'#607447':'#435d37');
                        const geo=new T.BufferGeometry();
                        geo.setAttribute('position',new T.Float32BufferAttribute([0,-.5,0,-.5,0,.12,0,.5,0,.5,0,.12],3));
                        geo.setAttribute('uv',new T.Float32BufferAttribute([.5,0,0,.5,.5,1,1,.5],2));
                        geo.setIndex([0,1,2,0,2,3]);geo.computeVertexNormals();
                        add(geo,x,y,z,pine?.006:.027+(birch?.005:.008)*noise(n+17),pine?.03:.035,pine?.016:.024,c,1,
                            .65+(noise(n+7)-.5)*.8,(noise(n+9)-.5)*.9,a+side*.7);
                    }
                }
            };
            const bend=[.035,.57,-.012],tip=[birch?.07:.025,.99,.018];
            if(!shrub)markTrunk(()=>{branch([0,0,0],bend,birch?.022:pine?.031:.041,birch?.014:.022);branch(bend,tip,birch?.014:.022,.004);});
            const n=shrub?10:pine?18:birch?13:11;
            for(let i=0;i<n;i++){
                const a=i*2.39996+.25,baseY=shrub?.03:pine?.48+(i/n)*.4:birch?.32+(i/n)*.5:.31+(i/n)*.48;
                const radius=shrub?.25:pine?.31*(1-(baseY-.48)*1.5):birch?.19+.05*noise(i):.3+.08*noise(i);
                const origin=[shrub?0:.025,baseY,0],fork=[Math.cos(a)*radius*.57,baseY+.08+.1*noise(i+45),Math.sin(a)*radius*.57];
                const end=[Math.cos(a)*radius,baseY+(pine?.05:birch?.12:.18),Math.sin(a)*radius];
                branch(origin,fork,shrub?.012:birch?.011:.017,.006);branch(fork,end,.006,.0025);
                for(const side of [-1,1]){
                    const twig=[end[0]+Math.cos(a+side*.7)*.07,end[1]+(birch?-.055:.035),end[2]+Math.sin(a+side*.7)*.07];
                    branch(fork,twig,.004,.0012);crown(twig,i*2+(side===1?1:0));
                    if(birch)branch(twig,[twig[0]+Math.cos(a)*.04,twig[1]-.07,twig[2]+Math.sin(a)*.04],.0018,.0006);
                }
            }
            if(birch)for(let i=0;i<13;i++){
                const y=.045+i*.063,x=y<.57?.035*y/.57:.035+(.07-.035)*(y-.57)/.42;
                B(x,y,.021,.025,.004,.008,'#494a40',2,0,(i%3-1)*.15);
            }
            if(!shrub)for(let i=0;i<5;i++){const a=i*1.26;branch([0,.05,0],[Math.cos(a)*.07,0,Math.sin(a)*.07],.013,.004);}
        }
    } else if(id==='monument'||id==='monument_ruined') {
        const ruined=id==='monument_ruined';
        B(0,.07,0,1,.14,1,'#7e8076',2);B(0,.19,0,.84,.1,.82,'#97998c',2);
        B(0,ruined?.37:.46,0,.58,ruined?.27:.44,.56,'#8c8c7f',2);
        B(0,.38,.286,.28,.14,.012,'#555d53',2);
        if(!ruined){
            // Standing human silhouette, coat, boots and separate head.
            for(const x of [-.09,.09]){rod([x,.56,0],[x,.88,0],.045,'#596451');B(x,.56,.035,.11,.055,.19,iron,2);}
            add(new T.CylinderGeometry(.14,.19,.4,10),0,1.02,0,1,1,.65,'#5d6755',2);
            ball(0,1.18,0,.18,.075,.11,'#64705b');
            ball(0,1.37,0,.085,.12,.08,'#63705b');C(0,1.27,0,.04,.08,'#63705b');
            B(0,1.37,.08,.027,.045,.032,'#63705b',2);B(0,1.31,.06,.08,.025,.03,'#596451',2);
            for(const x of [-.04,.04])B(x,1.12,.105,.035,.17,.014,'#4e5948',2,0,x<0?-.2:.2);
            rod([-.17,1.17,0],[-.21,.91,.04],.045,'#596451');rod([.17,1.17,0],[.27,1.04,.12],.043,'#596451');
        }else{
            stone(-.35,.14,.36,.25,.13,.17,'#888b7e',.4);stone(.34,.14,-.2,.22,.15,.2,'#8e8d80',.7);
            add(new T.BoxGeometry(1,1,1),.28,.16,.25,.18,.23,.4,'#596451',2,.6,.4,0,0);ball(-.36,.13,-.27,.12,.12,.11,'#63705b');
            for(const x of [-.12,.1])rod([x,.47,0],[x+.04,.64,.02],.012,rust);
        }
    } else if(['tv_crt','tv_flat','tv_wall'].includes(id)) {
        if(id==='tv_crt') {
            B(0,.55,-.04,.98,.8,.85,rubber,2);screen(-.07,.57,.41,.73,.58);
            for(const y of [.37,.49,.61])C(.405,y,.43,.035,.025,grey,2,Math.PI/2);
            for(const x of [-.32,.32])B(x,.08,0,.12,.16,.55,iron,2);
            rod([-.2,.94,-.2],[-.45,1.17,-.2],.008);rod([-.2,.94,-.2],[.2,1.17,-.2],.008);
        } else {B(0,.62,0,1,.76,.09,rubber,2);screen(0,.62,.05,.95,.7);
            if(id!=='tv_wall'){B(0,.17,-.015,.08,.26,.075,iron,2);B(0,.035,0,.62,.07,.65,iron,2);}}
    } else if(id==='sofa') {
        legs(.16,.39);B(0,.32,.04,.95,.35,.8,cloth,1);B(0,.72,-.36,1,.57,.23,cloth,1,-.1);
        for(const x of [-.46,.46])B(x,.51,0,.12,.5,.95,wood);
        for(const x of [-.23,.23]){B(x,.53,.08,.44,.12,.75,'#85816c',1);B(x,.73,-.24,.43,.36,.12,'#777a66',1);}
    } else if(['coffee_table','dining_bench','street_bench','sauna_bench'].includes(id)) {
        const bench=id!=='coffee_table';legs(bench?.47:.86,.4);slats(bench?.5:.92,1,.9,bench?4:3);
        if(id==='street_bench')for(const x of [-.4,.4]){B(x,.72,-.455,.04,.55,.045,iron,2);}
        if(id==='street_bench')for(const y of [.7,.87])B(0,y,-.4,1,.13,.06,wood);
        if(id==='sauna_bench'){slats(.95,1,.42,3);for(const x of [-.42,.42])B(x,.72,-.28,.07,.5,.07,wood);}
    } else if(['tv_stand','shop_counter','tool_cabinet','medical_cabinet'].includes(id)) {
        const medical=id==='medical_cabinet',tall=id.includes('cabinet');
        B(0,.5,0,.94,.94,.9,medical?white:wood,medical?2:0);B(0,.99,0,1,.04,1,grey,2);
        for(const x of [-.235,.235]){B(x,.53,.465,.44,.85,.025,medical?blue:edge,2);B(x<0?-.04:.04,.5,.49,.02,.12,.025,iron,2);}
        if(id==='tool_cabinet')for(const y of [.25,.4,.55,.7,.85]){B(0,y,.49,.85,.015,.02,grey,2);B(0,y+.055,.51,.3,.015,.025,iron,2);}
        if(medical){B(0,.14,.49,.88,.22,.03,white,2);for(const y of [.4,.65,.87])B(0,y,.48,.85,.025,.035,white,2);}
        if(!tall)for(const x of [-.4,.4])B(x,.025,0,.07,.05,.6,iron,2);
    } else if(['clothes_dryer','clothes_line'].includes(id)) {
        if(id==='clothes_dryer') {
            for(const x of [-.4,.4]){rod([x,.02,-.42],[x,.8,.35]);rod([x,.02,.42],[x,.8,-.35]);}
            for(const x of [-.5,.5])rod([x,.78,-.45],[x,.78,.45]);
            for(let i=0;i<7;i++)rod([-.5,.8,-.4+i*.13],[.5,.8,-.4+i*.13],.008,grey);
            for(const x of [-.8,.8]){rod([Math.sign(x)*.5,.78,-.45],[x,.94,-.45]);rod([Math.sign(x)*.5,.78,.45],[x,.94,.45]);}
        } else {
            for(const x of [-.48,.48])C(x,.5,0,.018,1,grey);
            for(const z of [-.2,.2])rod([-.48,.94,z],[.48,.94,z],.004,grey);
            for(let i=0;i<4;i++)B(-.34+i*.22,.66,0,.16,.49,.014,i%2?white:cloth,1,0,(i-.5)*.03);
        }
    } else if(['gas_boiler','water_heater','washing_machine','switchboard'].includes(id)) {
        if(id==='water_heater'){C(0,.5,0,.45,1,white);C(0,.03,0,.37,.05,grey);}
        else {B(0,.5,0,.96,.96,.9,id==='switchboard'?grey:white,2);B(0,.5,.46,.86,.85,.025,id==='switchboard'?iron:white,2);}
        if(id==='washing_machine') {ring(0,.44,.485,.3,.065,iron,0);C(0,.44,.48,.25,.025,blue,2,Math.PI/2);B(0,.88,.475,.88,.12,.035,grey,2);C(.32,.88,.51,.045,.03,dark,2,Math.PI/2);}
        else if(id==='switchboard') {B(0,.62,.49,.38,.24,.015,white,2);B(.35,.35,.5,.025,.16,.025,iron,2);}
        else {B(0,.23,.47,.42,.12,.025,iron,2);for(const x of [-.2,.2]){C(x,.21,.49,.04,.03,grey,2,Math.PI/2);C(x,-.05,0,.025,.15,iron);}if(id==='gas_boiler')C(0,1.05,-.12,.1,.18,grey);}
    } else if(['bucket_metal','bucket_plastic','tub','basin','pot','cauldron','bowl','glass','mug','kettle','pan'].includes(id)) {
        const c=id==='bucket_plastic'||id==='basin'?blue:id==='cauldron'||id==='pan'?iron:id==='tub'?wood:white;
        const height=id==='basin'||id==='pan'?.22:id==='bowl'?.4:.78;
        vessel(.4,height,c);
        if(['bucket_metal','bucket_plastic'].includes(id))ring(0,height,0,.38,.018,iron,0);
        if(['pot','cauldron'].includes(id)){handle(-.46,height*.75);handle(.46,height*.75);}
        if(id==='tub')for(const y of [.12,.5])ring(0,y,0,.34+y*.08,.025,iron);
        if(id==='mug')handle(.47,.45);
        if(id==='pan')B(0,.18,.7,.14,.09,.75,edge,2);
        if(id==='kettle'){rod([.27,.23,0],[.6,.65,0],.07,grey);ring(-.25,.55,0,.32,.035,edge,0);C(0,.8,0,.3,.04,grey);C(0,.85,0,.06,.08,edge);}
    } else if(['plate','fork','spoon','knife','cutting_board','table_setting','kitchen_set'].includes(id)) {
        const plate=(x,z,r=.4)=>{C(x,.04,z,r,.06,white);ring(x,.09,z,r*.94,.022,white);C(x,.078,z,r*.7,.012,'#aaa997');};
        const fork=(x,z)=>{B(x,.025,z,.045,.035,.55,grey,2);B(x,.025,z-.33,.16,.035,.18,grey,2);for(let i=0;i<4;i++)B(x-.065+i*.043,.025,z-.48,.023,.035,.19,grey,2);};
        if(id==='plate')plate(0,0);
        if(id==='fork')fork(0,.2);
        if(id==='spoon'){B(0,.04,.13,.07,.03,.65,grey,2);ball(0,.06,-.3,.17,.035,.24,grey);}
        if(id==='knife'){B(0,.05,.3,.16,.09,.4,edge);B(.03,.035,-.2,.2,.035,.64,grey,2);}
        if(id==='cutting_board'){B(0,.035,0,.88,.07,.8,wood);B(0,.035,.46,.3,.07,.18,wood);}
        if(id==='table_setting'){plate(0,0,.3);fork(-.4,.12);C(.35,.18,-.32,.1,.33,blue);}
        if(id==='kitchen_set'){C(-.2,.18,0,.24,.34,white);ring(-.2,.36,0,.23,.025,grey);C(.3,.1,0,.18,.12,iron);B(.3,.1,.3,.07,.07,.4,edge);C(.05,.2,-.2,.09,.4,blue);}
    } else if(id==='dish_rack') {
        B(0,.05,0,.95,.08,.9,grey,2);for(let i=0;i<6;i++){rod([-.4+i*.16,.08,-.4],[-.4+i*.16,.8,-.4],.012);rod([-.4+i*.16,.8,-.4],[-.4+i*.16,.8,.3],.012);}
        for(const x of [-.46,.46])rod([x,.1,-.4],[x,.1,.4]);
    } else if(id==='shower') {
        B(0,.04,0,1,.08,1,white,2);for(const z of [-.47,.47])B(0,.1,z,1,.12,.06,white,2);
        for(const x of [-.47,.47])B(x,.1,0,.06,.12,.9,white,2);
        rod([-.43,.15,-.43],[-.43,.94,-.43],.018);rod([-.43,.94,-.43],[-.1,.94,-.43],.018);C(-.1,.93,-.43,.09,.025,grey);
        rod([-.48,.98,.43],[.48,.98,.43],.014);for(let i=0;i<8;i++)B(-.4+i*.08,.57,.4,.075,.8,.025,i%2?cloth:white,1);
    } else if(['mirror','blackboard'].includes(id)) {
        B(0,.5,0,1,1,.1,id==='mirror'?grey:wood,2);B(0,.5,.06,.9,.9,.035,id==='mirror'?'#a9bdb4':'#364a3e',2);
        if(id==='blackboard'){B(0,.03,.13,.94,.04,.17,wood);for(let i=0;i<4;i++)B(-.2+i*.13,.56-i*.08,.09,.2,.006,.003,white,2);}
    } else if(id==='dumpster') {
        B(0,.13,0,.86,.09,.8,'#43534b',2);
        for(const x of [-.46,.46])B(x,.53,0,.06,.78,.9,'#526454',2);
        for(const z of [-.45,.45])B(0,.53,z,.91,.78,.055,'#526454',2);
        for(const x of [-.4,.4])for(const z of [-.34,.34])C(x,.055,z,.055,.05,rubber,2,0,Math.PI/2);
        for(let i=0;i<9;i++)ball(Math.sin(i*4)*.29,.53+(i%3)*.09,Math.cos(i*3)*.25,.17,.12,.14,i%2?'#45483e':'#77705b');
        for(let i=0;i<6;i++)B(Math.sin(i*3)*.28,.78,Math.cos(i*4)*.28,.25,.016,.16,i%2?white:'#97856c',3,.15,i*.2);
        B(0,1,-.45,.98,.06,.65,iron,2,-.8);
        for(const z of [-.49,.49])for(const y of [.25,.76])B(0,y,z,.94,.035,.025,'#36453a',2);
    } else if(['laundry_basket','shopping_basket','trash_bin'].includes(id)) {
        if(id==='trash_bin')vessel(.43,.9,grey);
        else {
            const c=id==='dumpster'?'#4e6758':id==='laundry_basket'?cloth:blue;
            B(0,.05,0,.9,.08,.86,c,2);
            for(const x of [-.45,.45])for(let i=0;i<7;i++)B(x,.14+i*.11,0,.045,.045,.9,c,2);
            for(const z of [-.45,.45])for(let i=0;i<7;i++)B(0,.14+i*.11,z,.9,.045,.045,c,2);
            for(const x of [-.43,.43])for(const z of [-.43,.43])B(x,.47,z,.045,.87,.045,iron,2);
            if(id==='dumpster'){B(0,.97,0,1,.08,1,iron,2);for(const x of [-.36,.36])C(x,.05,0,.09,.12,rubber,2,0,Math.PI/2);}
            if(id==='shopping_basket')for(const z of [-.4,.4]){rod([-.4,.85,z],[0,1.2,z]);rod([0,1.2,z],[.4,.85,z]);}
        }
    } else if(['radiator','towel_rail'].includes(id)) {
        for(const x of [-.43,.43])C(x,.5,0,.025,1,grey);
        for(let i=0;i<(id==='radiator'?9:6);i++) {
            if(id==='radiator')B(-.4+i*.1,.5,0,.065,.86,.55,white,2);
            else rod([-.43,.1+i*.16,0],[.43,.1+i*.16,0],.025,grey);
        }
    } else if(id==='gas_cylinder') {
        C(0,.45,0,.44,.8,rust);ball(0,.86,0,.43,.12,.43,rust);C(0,.99,0,.06,.15,iron);ring(0,1.04,0,.12,.022,iron);
    } else if(id==='broom'||id==='mop') {
        rod([0,.15,0],[.08,1,0],.022,wood);
        if(id==='broom')for(let i=0;i<12;i++)rod([0,.2,0],[-.4+i*.07,.01,0],.023,'#a69461');
        else for(let i=0;i<8;i++)B(-.4+i*.11,.04,0,.09,.07,.6,cloth,1);
    } else if(['display_case','refrigerated_display'].includes(id)) {
        B(0,.18,0,1,.35,1,white,2);
        for(const x of [-.47,.47])for(const z of [-.47,.47])B(x,.65,z,.035,.7,.035,iron,2);
        for(const y of [.45,.72,.98])B(0,y,0,.96,.025,.96,'#859c98',2);
        for(const x of [-.47,.47])B(x,.98,0,.035,.035,.98,iron,2);
        if(id==='refrigerated_display')B(0,.45,-.48,.95,.5,.045,white,2);
        B(0,.72,.48,.91,.51,.008,'#9ab9b4',2);
        for(const x of [-.48,.48])B(x,.72,0,.008,.51,.91,'#9ab9b4',2);
    } else if(id==='cash_register'||id==='shop_scales') {
        B(0,.12,0,1,.24,.8,grey,2);B(0,.3,-.1,.75,.15,.55,white,2);
        if(id==='cash_register'){screen(0,.6,-.2,.7,.4);for(let x=0;x<5;x++)for(let z=0;z<3;z++)B(-.25+x*.12,.39,.05+z*.1,.08,.04,.07,iron,2);}
        else {C(0,.45,.03,.4,.045,white);B(0,.7,-.32,.07,.5,.07,iron,2);screen(0,.9,-.32,.75,.2);}
    } else if(id==='workbench') {
        for(const x of [-.43,.43])for(const z of [-.4,.4])B(x,.37,z,.065,.74,.065,'#4e625b',2);
        B(0,.77,0,1,.11,.94,wood);B(0,.18,0,.86,.045,.77,edge);
        for(const x of [-.43,.43])B(x,.22,0,.055,.055,.85,iron,2);
        B(.23,.57,.04,.4,.25,.73,'#58685c',2);B(.23,.57,.425,.38,.22,.025,'#677970',2);B(.23,.58,.45,.15,.025,.035,iron,2);
        // Attached metal vice, jaws, screw and transverse handle.
        B(-.3,.85,.32,.24,.055,.27,iron,2);B(-.3,.94,.34,.2,.14,.15,grey,2);
        for(const z of [.24,.45])B(-.3,1.03,z,.25,.06,.045,grey,2);
        rod([-.3,.93,.27],[-.3,.93,.58],.018);rod([-.38,.93,.58],[-.22,.93,.58],.012);
        B(.18,.84,-.18,.28,.025,.06,iron,2,0,.3);B(.3,.85,-.26,.06,.035,.2,wood,0,0,.3);
    } else if(id==='pallet') {
        for(const x of [-.4,0,.4])B(x,.4,0,.14,.6,.85,wood);
        slats(.9,1,1,6);
    } else if(id==='vise') {
        B(0,.1,0,.7,.18,.7,iron,2);C(0,.24,0,.28,.15,grey);B(0,.48,0,.6,.35,.6,iron,2);
        for(const z of [-.29,.29])B(0,.72,z,.8,.18,.12,grey,2);rod([-.5,.4,.2],[.5,.4,.2],.025);C(.5,.4,.2,.07,.15,iron,2,0,Math.PI/2);
    } else if(id==='pallet_jack') {
        for(const x of [-.35,.35]){B(x,.1,.12,.18,.16,.85,rust,2);C(x,.05,.43,.07,.1,rubber,2,0,Math.PI/2);}
        B(0,.22,-.35,.8,.3,.2,rust,2);rod([0,.3,-.35],[0,.85,-.5],.04);ring(0,.92,-.5,.13,.035,iron,0);
    } else if(['tire','sandbag','sandbag_wall'].includes(id)) {
        if(id==='tire'){ring(0,.14,0,.32,.13,rubber);for(let i=0;i<16;i++){const a=i*Math.PI/8;B(Math.cos(a)*.42,.15,Math.sin(a)*.42,.045,.2,.05,iron,2,0,a);}}
        else for(let row=0;row<(id==='sandbag'?1:4);row++)for(let i=0;i<(id==='sandbag'?1:4);i++) {
            const x=id==='sandbag'?0:-.36+i*.24+(row%2?.06:0),y=.12+row*.2;
            ball(x,y,0,id==='sandbag'?.45:.15,.13,.32,'#9d9473');
        }
    } else if(id==='street_lamp'||id==='floor_lamp') {
        C(-.18,.03,0,.25,.06,iron);C(-.18,.48,0,.025,.9,iron);
        if(id==='street_lamp'){rod([-.18,.93,0],[.35,.99,0],.025);B(.35,.96,0,.28,.06,.3,white,2);}
        else {add(new T.CylinderGeometry(.12,.32,.24,16,1,true),-.18,.88,0,1,1,1,cloth,1);C(-.18,.85,0,.11,.06,white);}
    } else if(id==='power_tower'||id==='electric_pole') {
        if(id==='electric_pole'){C(0,.49,0,.035,.98,grey);for(const y of [.8,.94])B(0,y,0,1,.025,.04,iron,2);}
        else for(const sx of [-1,1])for(const sz of [-1,1]) {
            rod([sx*.43,0,sz*.43],[sx*.08,1,sz*.08],.014,grey);
            for(let i=0;i<5;i++) {
                const y=i*.16,a=.43-y*.35,b=.43-(y+.16)*.35;
                rod([sx*a,y,sz*a],[-sx*b,y+.16,sz*b],.007,iron);
                rod([sx*a,y,sz*a],[sx*b,y+.16,-sz*b],.007,iron);
            }
        }
        for(const y of [.72,.88]){B(0,y,0,1,.018,.045,iron,2);for(const x of [-.45,-.3,.3,.45])for(let i=0;i<3;i++)C(x,y-.02-i*.012,0,.025,.01,white);}
    } else if(id==='transformer') {
        B(0,.43,0,.94,.82,.9,grey,2);B(0,.87,0,1,.07,1,iron,2);B(0,.43,.46,.5,.7,.035,iron,2);
        for(const x of [-.35,.35])for(let i=0;i<7;i++)B(x,.3+i*.04,.47,.16,.014,.02,dark,2);
        B(0,.6,.49,.15,.15,.012,'#a99b4c',2);rod([-.065,.54,.5],[0,.65,.5],.008,dark);rod([0,.65,.5],[.065,.54,.5],.008,dark);
    } else if(id==='mailbox') {
        B(0,.45,0,.96,.88,.8,blue,2);B(0,.82,.42,.65,.045,.03,dark,2);B(0,.3,.42,.75,.28,.03,grey,2);
    } else if(id==='downpipe') {
        // Model in physical proportions: normalizing a short unit-height pipe
        // to 2.5 m stretched the tilted outlet's cross-section vertically.
        const radius=.065;
        C(0,1.3,0,radius,2.4,grey);
        rod([0,.1,0],[0,.065,.15],radius,grey);
        for(const y of [.45,2])ring(0,y,0,.095,.005,iron);
    } else if(id==='well') {
        vessel(.44,.46,grey);for(const x of [-.38,.38])B(x,.72,0,.05,.54,.07,wood);
        rod([-.42,.7,0],[.42,.7,0],.025,wood);C(0,.72,0,.07,.7,wood,2,0,Math.PI/2);
        for(const side of [-1,1])B(side*.22,.96,0,.53,.035,1,edge,0,0,side*.35);
    } else if(id==='road_sign') {
        // Physical proportions in the catalogue frame: circular 60 cm face
        // on a narrow post, without stretching the face across the whole width.
        C(0,.43,-.035,.018,.86,grey);C(0,.86,0,.135,.012,'#c1bdac',2,Math.PI/2);
        ring(0,.86,.015,.118,.012,'#8b4e42',0);
        B(0,.86,.023,.17,.025,.006,'#c1bdac',2);
    } else if(['car_sedan','car_van','car_burned'].includes(id)) {
        const van=id==='car_van',burned=id==='car_burned';
        const paint=burned?'#47443b':van?'#8a8975':'#687a72',rust='#70503b',glass=burned?'#222822':'#3c4946';
        B(0,.28,0,.91,.24,.95,paint,2);B(0,.18,0,.79,.08,.82,dark,2);
        B(0,.43,-.34,.88,.07,.27,burned?rust:paint,2);
        const cabin=new T.BoxGeometry(1,1,1),cp=cabin.attributes.position;
        for(let i=0;i<cp.count;i++)if(cp.getY(i)>0){cp.setX(i,cp.getX(i)*.87);cp.setZ(i,cp.getZ(i)*.82);}
        cabin.computeVertexNormals();add(cabin,0,van?.65:.61,van?.13:.05,.79,van?.65:.4,van?.68:.46,paint,2);
        B(0,van?.97:.82,van?.13:.05,.72,.045,van?.58:.39,burned?rust:paint,2);
        B(0,van?.78:.66,van?-.17:-.155,.62,van?.24:.22,.012,glass,2,.15);
        B(0,van?.78:.66,van?.43:.255,.62,van?.23:.21,.012,glass,2,-.15);
        for(const side of [-1,1]) {
            for(const z of (van?[-.09,.13,.35]:[-.075,.17])) {
                B(side*.372,van?.77:.66,z,.012,van?.22:.21,van?.15:.15,glass,2,0,side*.13);
                B(side*.408,.44,z,.012,.012,.034,iron,2);
            }
            for(const z of [-.31,.32]) {
                C(side*.445,.135,z,.135,.09,'#292d29',2,0,Math.PI/2);
                C(side*.495,.135,z,.074,.012,burned?rust:'#77776a',2,0,Math.PI/2);
            }
            B(side*.34,.36,-.485,.19,.065,.018,'#aaa68c',2);
            B(side*.34,.34,.485,.15,.05,.018,'#795248',2);
            B(side*.465,.3,.17,.014,.075,.16,rust,2);
            B(side*.46,.23,-.11,.016,.04,.22,rust,2);
        }
        for(const z of [-.495,.495])B(0,.22,z,.97,.055,.025,burned?rust:'#777a70',2);
        B(0,.3,.506,.23,.035,.012,'#a19e89',2);
        for(const x of [-.18,.18])B(x,.48,.1,.26,.18,.17,'#464b40',1);
        B(0,.37,-.508,.4,.055,.012,dark,2);
    } else if(id==='stump'||id==='hollow_tree') {
        const tall=id==='hollow_tree', top=tall ? .87 : .56;
        const bark='#504339',cut='#97846a';
        if(tall){
            C(0,.16,0,.35,.32,bark,0,0,0,.92);
            add(new T.CylinderGeometry(.305,.322,.32,16,1,true,.43,Math.PI*2-.86),
                0,.48,0,1,1,1,bark);
            C(0,.755,0,.305,.23,bark,0,0,0,.94);
            C(0,.48,0,.195,.32,'#30291f',0);
            for(const side of [-1,1])rod([side*.135,.32,.292],[side*.127,.64,.276],.029,edge);
        }else C(0,top/2,0,.35,top,bark,0,0,0,.85);
        C(0,top+.012,0,.291,.026,cut,0);
        for(const radius of [.09,.16,.23])ring(0,top+.027,0,radius,.007,wood);
        for(let i=0;i<7;i++){
            const angle=i*2.4,x=Math.cos(angle),z=Math.sin(angle);
            rod([x*.21,.21,z*.21],[x*.49,.02,z*.47],.035+.005*(i%3),bark);
            if(!tall||z<.85)rod([x*.32,.08,z*.32],[x*.28,top-.04,z*.28],.022,i%2?edge:bark);
        }
        if(tall){
            rod([-.03,.54,0],[-.33,.96,-.18],.071,bark);
        }
    } else if(id==='safe_small'||id==='safe_large') {
        B(0,.50,0,.96,.98,.92,'#4a514b',2);
        B(0,.51,.465,.88,.89,.022,'#292d29',2);
        B(0,.51,.483,.81,.82,.035,'#5a5d53',2);
        for(const y of [.23,.77])B(-.37,y,.511,.075,.13,.047,iron,2);
        if(id==='safe_large'){
            ring(.06,.53,.525,.137,.019,iron,0);
            for(const angle of [0,Math.PI/3,-Math.PI/3])
                B(.06,.53,.525,.245,.029,.023,iron,2,0,angle);
        }else B(.13,.51,.531,.14,.034,.052,iron,2);
        add(new T.CylinderGeometry(.055,.055,.024,12),.27,.63,.521,1,1,1,dark,2,Math.PI/2);
        B(.27,.63,.535,.008,.026,.004,enamel,2);
    } else if(id==='ground_stash') {
        for(let i=0;i<9;i++){
            const angle=i*2.4,r=.32*Math.sqrt(i/9);
            stone(Math.cos(angle)*r,.23,Math.sin(angle)*r,.09+(i%3)*.047,.16,.12,i%2?'#695847':'#55473a',angle);
        }
    } else if(['brick','concrete_chunk','broken_boards','rubble','stones','fallen_tree','bottle','tin_can','litter'].includes(id)) {
        if(id==='brick') {B(0,.14,0,.9,.28,.9,'#93644e',2,0,.08);for(const x of [-.23,0,.23])C(x,.29,0,.06,.008,dark);}
        if(id==='concrete_chunk'){stone(0,.28,0,.45,.3,.4);for(const x of [-.2,.2])rod([x,.3,0],[x+.15,.75,.2],.012,rust);}
        if(id==='broken_boards')for(const side of [-1,1]){
            add(new T.BoxGeometry(1,1,1),0,0,side*.22,.86,.035,.12,wood,0,0,0,side*.07,0);
            add(new T.BoxGeometry(1,1,1),side*.23,0,0,.12,.035,.83,wood,0,0,0,-side*.08,.035);
            add(new T.BoxGeometry(1,1,1),side*.08,0,0,.11,.035,.68,wood,0,0,0,side*.57,side>0?.105:.07);
        }
        if(id==='stones'||id==='rubble')for(let i=0;i<18;i++) {
            const a=i*2.4,r=.4*Math.sqrt(i/18),h=id==='stones'?.09:.2;
            if(id==='rubble'&&i%3===0)add(new T.BoxGeometry(1,1,1),Math.cos(a)*r,0,Math.sin(a)*r,.18,.07,.09,rust,2,.2,0,a,0);
            else stone(Math.cos(a)*r,h*(1-r),Math.sin(a)*r,.08+(i%3)*.025,h,.08,grey,a);
        }
        if(id==='fallen_tree') {
            add(new T.CylinderGeometry(.035,.085,.9,10),-.02,.14,0,1,1,1,'#63543e',0,0,-Math.PI/2);
            for(let i=0;i<7;i++){
                const x=-.26+i*.09,angle=i*2.4,tip=[x+.08+Math.sin(i)*.12,.18+Math.cos(angle)*.1,Math.sin(angle)*(.18+i*.025)];
                rod([x,.14,0],tip,.018-i*.0015,wood);
                for(const side of [-1,1])rod(tip,[tip[0]+.06+side*.04,tip[1]+side*.04,tip[2]+side*.08],.006,wood);
            }
            for(const side of [-1,1])rod([-.45,.12,0],[-.5,.05,side*.16],.025,edge);
        }
        if(id==='bottle'){C(0,.32,0,.25,.6,'#536c50');ball(0,.64,0,.25,.13,.25,'#536c50');C(0,.82,0,.11,.25,'#536c50');C(0,.96,0,.12,.04,grey);}
        if(id==='tin_can'){vessel(.4,.8,grey);for(const y of [.1,.7])ring(0,y,0,.4,.018,iron);}
        if(id==='litter')for(let i=0;i<7;i++)B(Math.sin(i*3)*.3,.006*Math.cos(.12)+.1*Math.sin(.12),Math.cos(i*4)*.3,.24,.012,.2,i%2?white:'#91866b',3,.12,0);
    } else if(id==='child_bed'||id==='exam_couch') {
        legs(.55,.38);B(0,.63,0,.94,.18,1,id==='child_bed'?cloth:blue,1);B(0,.76,-.32,.75,.12,.23,white,1);
        if(id==='child_bed')for(const z of [-.48,.48])B(0,.67,z,1,.6,.04,wood);
    } else if(id==='school_desk') {
        legs(.85,.4);B(0,.93,0,1,.07,1,wood);B(0,.66,-.4,.85,.35,.05,wood);B(0,.8,0,.85,.035,.75,edge);
    } else if(id==='sauna_stove') {
        B(0,.36,0,.9,.65,.8,iron,2);B(0,.28,.42,.5,.3,.025,dark,2);C(-.25,.82,-.28,.1,.65,iron);
        for(let i=0;i<8;i++)stone(Math.sin(i*2)*.3,.7+(i%2)*.08,Math.cos(i*2)*.25,.13,.1,.13);
    } else return false;
    return true;
}
