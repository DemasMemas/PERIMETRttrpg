import { tileSurfaceTriangles } from './locationTerrain.js';
const EPS=1e-6;
const sub=(a,b)=>a.map((v,i)=>v-b[i]);
const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const dot=(a,b)=>a.reduce((n,v,i)=>n+v*b[i],0);
function triangleHit(origin,direction,a,b,c){
    const e1=sub(b,a),e2=sub(c,a),p=cross(direction,e2),det=dot(e1,p);
    if(Math.abs(det)<1e-9)return null;
    const offset=sub(origin,a),u=dot(offset,p)/det;if(u < -EPS || u>1+EPS)return null;
    const q=cross(offset,e1),v=dot(direction,q)/det;if(v < -EPS || u+v>1+EPS)return null;
    const t=dot(e2,q)/det;return t>EPS&&t<1-EPS?t:null;
}
export function terrainLineHit(tiles,start,end,cache=new Map(),accept=()=>true){
    const direction=sub(end,start),dx=direction[0],dz=direction[2];
    let x=Math.floor(start[0]),z=Math.floor(start[2]),entered=0;
    const sx=dx>0?1:-1,sz=dz>0?1:-1,dtx=dx?Math.abs(1/dx):Infinity,dtz=dz?Math.abs(1/dz):Infinity;
    let tx=dx?((dx>0?x+1:x)-start[0])/dx:Infinity,tz=dz?((dz>0?z+1:z)-start[2])/dz:Infinity;
    const limit=tiles.length+Math.max(0,...tiles.map(row=>row.length))+4;
    for(let i=0;i<limit;i++){
        const leaving=Math.min(tx,tz,1),tile=tiles[z]?.[x],key=x+':'+z;
        if(tile&&tile.terrain!=='water'){
            let data=cache.get(key);
            if(!data){const triangles=tileSurfaceTriangles(tiles,x,z);data={triangles,maximum:Math.max(...triangles.flatMap(t=>t.map(p=>p[1])))};cache.set(key,data);}
            if(data.maximum>=Math.min(start[1]+direction[1]*entered,start[1]+direction[1]*leaving)-EPS){
                let best=null;
                for(const triangle of data.triangles){
                    const t=triangleHit(start,direction,...triangle);
                    if(t!==null){const hit={t,x:start[0]+dx*t,z:start[2]+dz*t,height:start[1]+direction[1]*t};
                        if(accept(hit)&&(!best||t<best.t))best=hit;}
                }
                if(best)return best;
            }
        }
        if(leaving>=1)break;
        entered=leaving;
        if(tx<=leaving){x+=sx;tx+=dtx;}
        if(tz<=leaving){z+=sz;tz+=dtz;}
    }
    return null;
}
// Семантика существующего opening_at: закрытый люк перекрывает даже лестничный проём.
export function terrainOpeningAt(objects,level,x,z){
    const levelOf=o=>Number(o.properties?.level??o.level??0);
    const contains=o=>{
        const p=o.properties||{},d=p.dimensions||{},defaults={stairs:[1.2,3.6],ladder:[.9,.9],hatch:[1,1],floor_opening:[1,1]}[o.type]||[2,2];
        const dx=x-(Number(o.tile_x)+.5+Number(p.offset_x||0)),dz=z-(Number(o.tile_y)+.5+Number(p.offset_z||0)),a=Number(p.rotation||0);
        return Math.abs(dx*Math.cos(a)-dz*Math.sin(a))<Number(d.width||defaults[0])/2-.00001
            &&Math.abs(dx*Math.sin(a)+dz*Math.cos(a))<Number(d.depth||defaults[1])/2-.00001;
    };
    const hatches=objects.filter(o=>o.type==='hatch'&&levelOf(o)===level&&contains(o));
    if(hatches.some(o=>!o.properties?.is_open))return false;
    return Boolean(hatches.length)||objects.some(o=>(o.type==='floor_opening'&&levelOf(o)===level
        ||['stairs','ladder'].includes(o.type)&&levelOf(o)+1===level)&&contains(o));
}
