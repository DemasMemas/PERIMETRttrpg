export function waterFaceNormal(a,i) {
    const ux=a[i+3]-a[i],uy=a[i+4]-a[i+1],uz=a[i+5]-a[i+2],
        vx=a[i+6]-a[i],vy=a[i+7]-a[i+1],vz=a[i+8]-a[i+2];
    return [uy*vz-uz*vy,uz*vx-ux*vz,ux*vy-uy*vx];
}
export function smoothWaterNormals(positions,halo=[],shoulders=null) {
    const normals=new Map(),key=(a,i)=>[a[i],a[i+1],a[i+2]].map(n=>Math.round(n*1e6)).join(',');
    for(const a of [positions,halo])for(let i=0;i<a.length;i+=9){
        const n=waterFaceNormal(a,i);for(const j of [i,i+3,i+6]){const k=key(a,j),sum=normals.get(k)||[0,0,0];
            for(let axis=0;axis<3;axis++)sum[axis]+=n[axis];normals.set(k,sum);}
    }
    const result=[];
    for(let i=0;i<positions.length;i+=3){
        const [x,y,z]=normals.get(key(positions,i)),length=Math.hypot(x,y,z)||1;
        const shoulder=shoulders?.[i/3]||0;
        // Верх плавно продолжает грунт на стыке, затем проявляется наклон берега.
        const nx=x/length*(1-shoulder),ny=y/length*(1-shoulder)+shoulder,nz=z/length*(1-shoulder);
        const norm=Math.hypot(nx,ny,nz)||1;
        result.push(nx/norm,ny/norm,nz/norm);
    }
    return result;
}

export function shoreOwners(positions,tiles,originX=0,originZ=0) {
    const owners=[];
    for(let i=0;i<positions.length;i+=3){
        const px=positions[i],pz=positions[i+2];let owner=null,distance=Infinity;
        for(let z=Math.floor(pz)-1;z<=Math.floor(pz)+1;z++)for(let x=Math.floor(px)-1;x<=Math.floor(px)+1;x++){
            if(!tiles[z]?.[x]||tiles[z][x].terrain==='water')continue;
            const dx=Math.max(x-px,0,px-x-1),dz=Math.max(z-pz,0,pz-z-1),d=dx*dx+dz*dz;
            if(d<distance-1e-10){owner=[x+originX,z+originZ];distance=d;}
        }
        owners.push(...(owner||[px+originX,pz+originZ]));
    }
    return owners;
}
