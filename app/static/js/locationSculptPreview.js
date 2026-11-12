import { buildSmoothTerrainData, buildTerrainSurfaceData } from './locationTerrain.js';

// Temporary 8m regions during a stroke. The ordinary merged meshes, materials
// and textures remain in use outside these regions and after the commit.
export function createLocationSculptPreview(T, scene, tiles, sides, surface, foundation) {
    const size = 8, regions = new Map(), masks = [];
    let builds = 0, cellsBuilt = 0;
    for (const mesh of [sides, surface]) {
        if (!mesh) continue;
        const geometry = mesh.geometry, p = geometry.attributes.position;
        const original = geometry.index;
        const index = original ? original.clone() : new T.BufferAttribute(Uint32Array.from({length:p.count}, (_,i)=>i),1);
        const byRegion = new Map();
        for(let i=0;i<index.count;i+=3){
            const ids=[index.getX(i),index.getX(i+1),index.getX(i+2)];
            const tile = mesh===sides ? mesh.userData.tileByFace[i/3] : {
                x:Math.floor(ids.reduce((sum,id)=>sum+p.getX(id),0)/3),
                z:Math.floor(ids.reduce((sum,id)=>sum+p.getZ(id),0)/3),
            };
            const key=`${Math.floor(tile.x/size)},${Math.floor(tile.z/size)}`;
            if(!byRegion.has(key))byRegion.set(key,[]);
            byRegion.get(key).push(i);
        }
        geometry.setIndex(index);
        masks.push({geometry,original,index,byRegion});
    }
    const geometryFor = (data, cap) => {
        const g=new T.BufferGeometry();
        g.setAttribute('position',new T.Float32BufferAttribute(data.positions,3));
        g.setAttribute('normal',new T.Float32BufferAttribute(data.normals,3));
        if(cap){g.setAttribute('uv',new T.Float32BufferAttribute(data.uvs,2));g.setIndex(data.indices);}
        else {
            g.setAttribute('terrainTop',new T.Float32BufferAttribute(data.topByVertex,1));
            const colors=[],uv=[],coords=[];
            data.tileByVertex.forEach(({x,z},i)=>{
                // Match the existing sides' vertex colors and world-space UVs.
                colors.push(1,1,1);coords.push(x,z);
                uv.push(data.positions[i*3]*.125,data.positions[i*3+2]*.125);
            });
            g.setAttribute('color',new T.Float32BufferAttribute(colors,3));
            g.setAttribute('uv',new T.Float32BufferAttribute(uv,2));
            g.setAttribute('terrainTileCoord',new T.Float32BufferAttribute(coords,2));
        }
        return g;
    };
    return {
        update(changed, colorAt, nextFoundation, limitRelief) {
            const dirty=new Set();
            if(nextFoundation!==foundation){for(const key of regions.keys())dirty.add(key);foundation=nextFoundation;}
            for(const {x,z} of changed)for(let dz=-1;dz<=1;dz++)for(let dx=-1;dx<=1;dx++){
                if(tiles[z+dz]?.[x+dx])dirty.add(`${Math.floor((x+dx)/size)},${Math.floor((z+dz)/size)}`);
            }
            for(const key of dirty){
                const [cx,cz]=key.split(',').map(Number);
                const bounds={minX:cx*size,minZ:cz*size,maxX:Math.min(tiles[0].length-1,cx*size+size-1),maxZ:Math.min(tiles.length-1,cz*size+size-1)};
                // One halo cell supplies the same shared normals as the merged mesh.
                const halo={minX:Math.max(0,bounds.minX-1),minZ:Math.max(0,bounds.minZ-1),maxX:Math.min(tiles[0].length-1,bounds.maxX+1),maxZ:Math.min(tiles.length-1,bounds.maxZ+1)};
                const full=buildSmoothTerrainData(tiles,()=>true,foundation,halo);
                const data={positions:[],normals:[],topByVertex:[],tileByVertex:[],tileByFace:[]};
                full.tileByFace.forEach((tile,face)=>{
                    if(tile.x<bounds.minX||tile.x>bounds.maxX||tile.z<bounds.minZ||tile.z>bounds.maxZ)return;
                    data.tileByFace.push(tile);
                    data.positions.push(...full.positions.slice(face*9,face*9+9));
                    data.normals.push(...full.normals.slice(face*9,face*9+9));
                    data.topByVertex.push(...full.topByVertex.slice(face*3,face*3+3));
                    data.tileByVertex.push(tile,tile,tile);
                });
                const cap=buildTerrainSurfaceData(tiles,full,bounds,limitRelief);
                let pair=regions.get(key);
                if(!pair){
                    pair=[new T.Mesh(undefined,sides.material),new T.Mesh(undefined,surface.material)];
                    for(const mesh of pair){mesh.receiveShadow=true;mesh.raycast=()=>{};mesh.userData.sculptRegion=key;scene.add(mesh);}
                    regions.set(key,pair);
                    for(const mask of masks){
                        for(const i of mask.byRegion.get(key)||[]){const first=mask.index.getX(i);mask.index.setX(i+1,first);mask.index.setX(i+2,first);}
                        mask.index.needsUpdate=true;
                    }
                }
                const sideGeometry=geometryFor(data,false);
                const colors=new Map();
                data.tileByVertex.forEach(({x,z},i)=>{const key=`${x},${z}`;if(!colors.has(key))colors.set(key,colorAt(x,z));const c=colors.get(key);sideGeometry.attributes.color.setXYZ(i,c.r,c.g,c.b);});
                pair[0].geometry.dispose();pair[0].geometry=sideGeometry;
                pair[1].geometry.dispose();pair[1].geometry=geometryFor(cap,true);
                builds++;cellsBuilt+=(halo.maxX-halo.minX+1)*(halo.maxZ-halo.minZ+1);
            }
        },
        diagnostics:()=>({regions:regions.size,builds,cellsBuilt}),
        dispose(){
            for(const pair of regions.values())for(const mesh of pair){scene.remove(mesh);mesh.geometry.dispose();}
            // Release the temporary GPU index as well as region buffers.
            for(const {geometry,original} of masks){geometry.dispose();geometry.setIndex(original);}
        },
    };
}
