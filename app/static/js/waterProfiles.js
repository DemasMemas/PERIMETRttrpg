// Shared appearance rules. No geometry, heights, objects or random state.
export const WATER_PROFILES = [
    ['auto','Авто'],['clean','Чистая'],['neutral','Нейтральная'],['sandy','Песчаная'],
    ['peat','Болотная'],['mineral','Каменно-бетонная'],['industrial','Индустриальная'],
];
const VALUES = {
    clean:[0,0,0,0], neutral:[.10,.10,.08,0], sandy:[.84,.06,.02,0],
    peat:[.06,.86,.02,0], mineral:[.07,.03,.82,0], industrial:[.18,.12,.20,.68],
};
export const normalizeWaterProfile = value => WATER_PROFILES.some(([id])=>id===value)?value:'auto';
export function terrainWaterComponents(tile) {
    switch(tile?.terrain){
        case 'sand': return VALUES.sandy;
        case 'swamp': case 'shallow_water': return VALUES.peat;
        case 'rock': case 'gravel': case 'concrete': case 'granite_tiles': return VALUES.mineral;
        case 'metal': case 'rusty_metal': return VALUES.industrial;
        case 'snow': return VALUES.clean;
        case 'bare_dirt': case 'dirt': return [.24,.20,.08,0];
        default: return VALUES.neutral;
    }
}
export const WATER_CONTEXT_RADIUS = 3;
export function waterComponentsAt(getTile,x,z,basinKind) {
    const tile=getTile(x,z),manual=normalizeWaterProfile(tile?.water_profile);
    if(tile?.terrain!=='water')return terrainWaterComponents(tile);
    if(manual!=='auto')return VALUES[manual];
    if(basinKind==='swamp')return VALUES.peat;
    const sum=[0,0,0,0];let weight=0,total=0,nearest=Infinity;
    for(let dz=-3;dz<=3;dz++)for(let dx=-3;dx<=3;dx++){
        if(!dx&&!dz)continue;
        const distance=Math.hypot(dx,dz),w=1/(distance*distance),neighbor=getTile(x+dx,z+dz);
        if(!neighbor)continue;
        total+=w;
        if(neighbor.terrain==='water')continue;
        const components=terrainWaterComponents(neighbor);
        nearest=Math.min(nearest,distance);weight+=w;
        components.forEach((v,i)=>sum[i]+=v*w);
    }
    if(!weight)return VALUES.neutral;
    // Enclosed puddles follow their surroundings strongly. Farther into large
    // lakes the influence fades to neutral; no connected-basin scan is needed.
    const strength=Math.min(1,weight/total*2)*Math.min(1,1.6/nearest);
    return sum.map((v,i)=>VALUES.neutral[i]*(1-strength)+v/weight*strength);
}

export function connectedWaterCells(getTile,x,z) {
    if(getTile(x,z)?.terrain!=='water')return [];
    const cells=[[x,z]],seen=new Set([`${x},${z}`]);
    for(let i=0;i<cells.length;i++){
        const [cx,cz]=cells[i];
        for(const [dx,dz]of [[0,-1],[1,0],[0,1],[-1,0]]){
            const nx=cx+dx,nz=cz+dz,k=`${nx},${nz}`;
            if(!seen.has(k)&&getTile(nx,nz)?.terrain==='water'){seen.add(k);cells.push([nx,nz]);}
        }
    }
    return cells;
}

export function createWaterProfileLayer(THREE,getTile,getBasinKind) {
    const uniforms={waterProfiles:{value:null},waterProfilesSize:{value:new THREE.Vector2(1,1)}};
    let texture,width=0,height=0,queued=false,disposed=false,revision=0;
    const pending=new Set();
    const flush=()=>{
        queued=false;if(disposed||!texture)return;
        let dirty=false;
        for(const index of pending){
            const x=index%width,z=Math.floor(index/width);
            const values=waterComponentsAt(getTile,x,z,getBasinKind?.(x,z));
            for(let c=0;c<4;c++){
                const value=Math.round(Math.max(0,Math.min(1,values[c]))*255),offset=index*4+c;
                if(texture.image.data[offset]!==value){texture.image.data[offset]=value;dirty=true;}
            }
        }
        pending.clear();if(dirty){texture.needsUpdate=true;revision++;}
    };
    const layer={uniforms,
        resize(w,h){
            if(w===width&&h===height)return;
            texture?.dispose();width=w;height=h;pending.clear();
            texture=new THREE.DataTexture(new Uint8Array(w*h*4),w,h,THREE.RGBAFormat,THREE.UnsignedByteType);
            texture.magFilter=texture.minFilter=THREE.LinearFilter;texture.generateMipmaps=false;
            texture.colorSpace=THREE.LinearSRGBColorSpace;texture.needsUpdate=true;
            uniforms.waterProfiles.value=texture;uniforms.waterProfilesSize.value.set(w,h);
        },
        updateRegion(x,z,w=1,h=1){
            if(disposed)return;
            for(let nz=Math.max(0,z-3);nz<Math.min(height,z+h+3);nz++)
                for(let nx=Math.max(0,x-3);nx<Math.min(width,x+w+3);nx++)pending.add(nz*width+nx);
            if(!queued){queued=true;queueMicrotask(flush);}
        },
        flush,
        diagnostics(){return {width,height,bytes:width*height*4,revision,pending:pending.size};},
        dispose(){disposed=true;pending.clear();texture?.dispose();},
    };
    layer.resize(1,1);return layer;
}

export function waterProfileControls(id) {
    const section=document.createElement('section');section.className='map-editor-section';
    section.innerHTML=`<strong class="map-editor-section-heading">Профиль воды</strong>
        <label>Вид воды <select id="${id}-water-profile">${WATER_PROFILES.map(([value,label])=>`<option value="${value}">${label}</option>`).join('')}</select></label>
        <label><input type="checkbox" id="${id}-water-basin"> Весь связанный водоём</label>
        <p class="map-editor-hint">Кисть создаёт воду с выбранным профилем и меняет существующую. «Авто» учитывает окружающий грунт.</p>`;
    return section;
}
