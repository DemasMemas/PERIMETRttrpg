// The room graph is transient. Only ordinary location objects are persisted.
import { planEditableRoofs, roofBodyDefaults } from './locationRoofSections.js';
const WALL_HEIGHT = 2.4;
const WALL_DEPTH = 0.2;
const DOOR_WIDTH = 0.9;

export function shellBodyRects(shape,width,depth) {
    const horizontal={x:0,z:0,width,depth:4},vertical={x:0,z:0,width:4,depth};
    return shape==='rectangle'?[{x:0,z:0,width,depth}]
        :shape==='L'?[horizontal,vertical]
        :shape==='U'?[horizontal,vertical,{...vertical,x:width-4}]
        :shape==='C'?[vertical,horizontal,{...horizontal,z:depth-4}]
        :shape==='T'?[horizontal,{...vertical,x:(width-4)/2}]
        :shape==='cross'?[{...horizontal,z:(depth-4)/2},{...vertical,x:(width-4)/2}]
        :[horizontal,vertical,{...vertical,x:width-4,depth:depth-2}];
}

// Empty shells use the same editable primitives and signed levels as manual building.
// Two metre modules keep openings clear of corners and cap the number of objects.
export function planBuildingShell({ minX, minZ, width, depth, shape = 'rectangle',
    storeys = 1, basements = 0, wealth = 'ordinary', condition = 'intact', buildingType = 'residential',
    seed = '0', buildingId, roofType, roofPitch = 25, roofDirection = 'auto', roofOverhang = .25,
    bodies: requestedBodies = null, groundHeight = () => 0 }) {
    if (![minX, minZ, width, depth, storeys, basements].every(Number.isInteger)
        || width < 6 || depth < 6 || width > 16 || depth > 16 || width % 2 || depth % 2
        || storeys < 1 || storeys > 4 || basements < 0 || basements > 2 || !buildingId
        || !['rectangle', 'L', 'U', 'C', 'compound', 'T', 'cross', 'custom'].includes(shape)
        || !['residential','shop','warehouse','workshop','cafe','clinic','office','school','garage','bathhouse'].includes(buildingType)
        || !['poor', 'ordinary', 'rich'].includes(wealth)
        || !['intact', 'worn', 'damaged', 'ruins'].includes(condition)) {
        return { error: 'Размеры: чётные числа от 6 до 16 м; этажи 1–4, подвалы 0–2' };
    }
    if (shape !== 'rectangle' && (width < 10 || depth < 10)) {
        return { error: 'Для фигурного контура нужны размеры от 10×10 м' };
    }
    const bodyRects = requestedBodies || shellBodyRects(shape,width,depth);
    if (!Array.isArray(bodyRects) || !bodyRects.length || bodyRects.length > 16
        || bodyRects.some(b=>![b.x,b.z,b.width,b.depth].every(Number.isInteger)
            || b.x<0 || b.z<0 || b.width<2 || b.depth<2 || b.x+b.width>width || b.z+b.depth>depth
            || !Number.isInteger(b.storeys??storeys) || (b.storeys??storeys)<1 || (b.storeys??storeys)>4)) return {error:'Корпуса: целые размеры от 2 м внутри контура, 1–4 этажа'};
    const connected=new Set([0]);
    for(let pass=0;pass<bodyRects.length;pass++)bodyRects.forEach((a,i)=>{
        if(connected.has(i))return;
        if(bodyRects.some((b,j)=>connected.has(j)&&(()=>{
            const x=Math.min(a.x+a.width,b.x+b.width)-Math.max(a.x,b.x);
            const z=Math.min(a.z+a.depth,b.z+b.depth)-Math.max(a.z,b.z);
            return x>=0&&z>=0&&(x>0||z>0);
        })()))connected.add(i);
    });
    if(connected.size!==bodyRects.length)return {error:'Корпуса должны соединяться сторонами или перекрываться; касания одним углом недостаточно'};
    const roofBodies=bodyRects.map((b,i)=>roofBodyDefaults({...b,x:minX+b.x,z:minZ+b.z,
        level:(b.storeys??storeys)-1,type:b.type||roofType||'flat',pitch:b.pitch??roofPitch,
        direction:b.direction||(roofDirection==='auto'?undefined:roofDirection),eave:b.eave??2.4,overhang:b.overhang??roofOverhang},i));
    const unit = bodyRects.every(b=>[b.x,b.z,b.width,b.depth].every(v=>v%2===0)) ? 2 : 1;
    const cols = width / unit, rows = depth / unit;
    const occupied = (x, z, level=0) => {
        if (x < 0 || z < 0 || x >= cols || z >= rows) return false;
        if (roofType || requestedBodies || ['T','cross','custom'].includes(shape)) return bodyRects.some(b=>
            x*unit+unit/2>b.x && x*unit+unit/2<b.x+b.width && z*unit+unit/2>b.z && z*unit+unit/2<b.z+b.depth && level<(b.storeys??storeys));
        if (shape === 'L') return x < 2 || z < 2;
        if (shape === 'U') return x < 2 || x >= cols - 2 || z < 2;
        if (shape === 'C') return x < 2 || z < 2 || z >= rows - 2;
        if (shape === 'compound') return x < 2 || z < 2 || (x >= cols - 2 && z < rows - 1);
        return true;
    };
    const cells = [];
    for (let z = 0; z < rows; z++) for (let x = 0; x < cols; x++) {
        if (occupied(x, z)) cells.push({ x, z });
    }
    const generation = { version: 1, minX, minZ, width, depth, shape, storeys, basements,
        wealth, condition, buildingType, seed: String(seed), ...(roofType?{roofType,roofPitch,roofDirection,bodies:bodyRects}: {}) };
    const objects = [];
    const survivingCells=new Map(),wallDebris=[];
    const style = {
        poor: { wall: 'wall', floor: 'art_aged_wood', color: '#877964', thickness: .16, window: .8 },
        ordinary: { wall: 'art_worn_plaster', floor: 'floor', color: '#aaa18e', thickness: .2, window: 1.1 },
        rich: { wall: 'art_worn_plaster', floor: 'art_aged_wood', color: '#d4c9ae', thickness: .3, window: 1.5 },
    }[wealth];
    const random = key => hashSeed(`${seed}:${key}`) / 4294967296;
    const add = (type, level, x, z, w, d, h, extra = {}) => {
        const tile_x = Math.min(minX + width - 1, Math.max(minX, Math.floor(x)));
        const tile_y = Math.min(minZ + depth - 1, Math.max(minZ, Math.floor(z)));
        objects.push({ type, name: `Здание · ${type}`, tile_x, tile_y, properties: {
            building_id: buildingId, level, dimensions: { width: w, depth: d, height: h },
            offset_x: x - tile_x - .5, offset_z: z - tile_y - .5,
            elevation: 0, generation_seed: String(seed), building_generation: generation, ...extra,
        } });
    };
    // Keep the entrance and vertical circulation strip intact. Damage elsewhere is
    // missing geometry, never a colour-only effect or a phantom stair connection.
    const highestStorey=Math.max(...bodyRects.map(b=>b.storeys??storeys));
    const entry=cells.find(c=>!occupied(c.x,c.z-1));
    const entryX=minX+entry.x*unit+unit/2,entryZ=minZ+entry.z*unit+unit/2;
    // Two parallel flights alternate direction, sharing clear landings at both
    // ends. Narrow six metre houses use a shorter, steeper flight, never a ladder.
    const serviceSizes=highestStorey>1?[[4,8],[8,4],[4,6],[6,4]]:[[4,4]];
    let service=null;
    for(const [w,d] of serviceSizes) {
        const candidates=cells.filter(c=>Array.from({length:w/unit},(_,i)=>i).every(i=>
            Array.from({length:d/unit},(_,j)=>j).every(j=>occupied(c.x+i,c.z+j,highestStorey-1))));
        candidates.sort((a,b)=>Math.hypot(a.x-entry.x,a.z-entry.z)-Math.hypot(b.x-entry.x,b.z-entry.z));
        if(candidates.length){service={...candidates[0],width:w,depth:d,alongX:w>d};break;}
    }
    if(!service)return {error:'Для лестничных маршей нужно место не меньше 4×6 м на всех наземных этажах основного корпуса'};
    const serviceX=minX+service.x*unit,serviceZ=minZ+service.z*unit;
    const span=Math.max(service.width,service.depth),flightDepth=span>=8?3.6:2;
    const flightPoint=(lane,along)=>service.alongX?{x:serviceX+along,z:serviceZ+lane}:{x:serviceX+lane,z:serviceZ+along};
    const cellarCandidates=[];
    for(let z=minZ+1.5;z<minZ+depth-1;z++)for(let x=minX+1.5;x<minX+width-1;x++) {
        if(![-1,1].every(dx=>[-1,1].every(dz=>occupied(Math.floor((x+dx-minX)/unit),Math.floor((z+dz-minZ)/unit)))))continue;
        const u=service.alongX?z-serviceZ:x-serviceX,v=service.alongX?x-serviceX:z-serviceZ;
        if(highestStorey>1&&u>0&&u<4&&v>0&&v<span)continue;
        cellarCandidates.push({x,z});
    }
    const cellarScore=p=>Math.hypot(p.x-(serviceX+service.width/2),p.z-(serviceZ+service.depth/2))
        +(Math.hypot(p.x-entryX,p.z-entryZ)<3?20:0);
    cellarCandidates.sort((a,b)=>cellarScore(a)-cellarScore(b));
    const cellar=cellarCandidates[0];
    const cellarOther=cellarCandidates.find(p=>Math.hypot(p.x-cellar.x,p.z-cellar.z)>=2);
    if(basements&&(!cellar||basements>1&&!cellarOther))return {error:'Не удалось разместить подвальные люки вне межэтажной лестницы'};
    const circulation=[{x:serviceX+service.width/2,z:serviceZ+service.depth/2},...basements?[cellar,...(cellarOther?[cellarOther]:[])]:[]];
    for (let level = -basements; level < highestStorey; level++) {
        const surviving=new Set();survivingCells.set(level,surviving);
        for (const { x, z } of cells) {
            if(!occupied(x,z,level))continue;
            const cx=minX+x*unit+unit/2,cz=minZ+z*unit+unit/2;
            const protectedCell = x*unit < service.x*unit+service.width && z*unit < service.z*unit+service.depth
                && x>=service.x && z>=service.z || x===entry.x&&z===entry.z
                || circulation.some(p=>Math.abs(cx-p.x)<=unit&&Math.abs(cz-p.z)<=unit
                    || Math.abs(cz-entryZ)<unit&&cx>=Math.min(entryX,p.x)-unit/2&&cx<=Math.max(entryX,p.x)+unit/2
                    || Math.abs(cx-p.x)<unit&&cz>=Math.min(entryZ,p.z)-unit/2&&cz<=Math.max(entryZ,p.z)+unit/2);
            const collapse = level > -basements && !protectedCell
                && random(`slab:${level}:${x}:${z}`) < (condition === 'ruins' ? .48 : condition === 'damaged' ? .2 : 0);
            if (!collapse) add('floor', level, minX + x * unit + unit/2, minZ + z * unit + unit/2, unit, unit, .12,
                { roof_enabled: false, visual_asset: style.floor, color: '#776d5d' });
            else add('floor_opening', level, minX + x * unit + unit/2, minZ + z * unit + unit/2, unit, unit, .05,
                {structural_damage:{version:1,material:'wood',seed:`${seed}:slab:${level}:${x}:${z}`}});
            if(!collapse)surviving.add(`${x}:${z}`);
            if (!roofType && level === storeys - 1 && random(`roof:${x}:${z}`)
                >= (condition === 'ruins' ? .85 : condition === 'damaged' ? .4 : condition === 'worn' ? .08 : 0)) {
                add('roof', level, minX + x * 2 + 1, minZ + z * 2 + 1, 2, 2, .13,
                    { elevation: 2.4, passable: true, color: wealth === 'poor' ? '#665b4c' : '#575b52' });
            }
            for (const [dx, dz] of [[0, -1], [0, 1], [-1, 0], [1, 0]]) {
                if (occupied(x + dx, z + dz, level)) continue;
                const entrance = x === entry.x && z === entry.z && dz === -1;
                const roll = random(`wall:${level}:${x}:${z}:${dx}:${dz}`);
                const broken = !entrance && level >= 0
                    && roll < (condition === 'ruins' ? .72 : condition === 'damaged' ? .3 : 0);
                const wx = minX + x * unit + unit/2 + dx*unit/2, wz = minZ + z * unit + unit/2 + dz*unit/2;
                const rotation = dx ? -Math.PI / 2 : 0;
                const damageSeed=`${seed}:wall:${level}:${x}:${z}:${dx}:${dz}`;
                if(broken)wallDebris.push({level,x,z,wx,wz,dx,dz,rotation,seed:damageSeed});
                if (broken && roll < (condition === 'ruins' ? .4 : .14)) continue;
                const height = broken ? .45 + roll : 2.4;
                add('wall', level, wx, wz, unit, style.thickness, height,
                    { rotation, color: style.color, visual_asset: style.wall, exterior_wall: true,
                        ...(broken?{structural_damage:{version:1,seed:damageSeed}}:{}) });
                const windowMargin=Math.min(unit*.8,style.window)/2+.25;
                const nearStairs=highestStorey>1&&wx>=serviceX-windowMargin&&wx<=serviceX+service.width+windowMargin
                    &&wz>=serviceZ-windowMargin&&wz<=serviceZ+service.depth+windowMargin
                    ||basements>0&&level===0&&Math.hypot(wx-cellar.x,wz-cellar.z)<1.5+windowMargin;
                if (entrance && level === 0) {
                    add('door', level, wx, wz, Math.min(unit*.9,wealth === 'rich' ? 1.3 : 1), .18, 2,
                        { rotation, is_open: true, ...(condition === 'ruins' ? { visual_asset: 'opening', interactions: [] } : {}) });
                } else if (level >= 0 && !broken && !entrance && !nearStairs
                    && (x + z) % (wealth === 'poor' ? 3 : 2) === 1
                    && occupied(x + (dz ? -1 : 0), z + (dx ? -1 : 0), level)
                    && occupied(x + (dz ? 1 : 0), z + (dx ? 1 : 0), level)) {
                    const lost = condition !== 'intact' && random(`window:${level}:${x}:${z}:${dx}:${dz}`)
                        < (condition === 'worn' ? .2 : .6);
                    add('window', level, wx, wz, Math.min(unit*.8,style.window), .14, 1,
                        { rotation, elevation: 1, ...(lost ? { is_open: true } : {}) });
                }
            }
        }
        if (level < highestStorey - 1) {
            if(level<0) {
                const p=Math.abs(level)%2?cellar:cellarOther||cellar;
                add('ladder',level,p.x,p.z,.9,.9,2.4,{elevation:.035});
                add('hatch',level+1,p.x,p.z,1,1,.08,{elevation:.035,is_open:false});
            } else {
                const p=flightPoint(level%2?2.5:1.5,span/2);
                const rotation=(service.alongX?Math.PI/2:0)+(level%2?Math.PI:0);
                const broken=condition==='ruins'&&level===highestStorey-2;
                if(!broken)add('stairs',level,p.x,p.z,.9,flightDepth,2.4,{rotation,elevation:.035});
                else add('floor_opening',level+1,p.x,p.z,.9,flightDepth,.05,{rotation});
            }
        }
        if(level>=0&&highestStorey>1)for(const lane of [.9,3.1]) {
            const p=flightPoint(lane,span/2);
            add('wall',level,p.x,p.z,flightDepth,.14,2.4,{rotation:service.alongX?0:Math.PI/2,visual_asset:'inner_wall',stairwell:true});
        }
    }
    // Compact only compatible rectangles. Openings remain separate editable objects;
    // the existing wall/floor geometry cuts them out of the resulting spans.
    compactShellObjects(objects, condition);
    // Обломки падают до первого уцелевшего перекрытия. Проход лестницы и
    // вход остаются свободны; каждый завал — обычный редактируемый объект.
    for(const source of wallDebris.slice(0,48)){
        const {x,z,wx,wz,dx,dz,rotation,seed:debrisSeed}=source;
        let landing=source.level;
        while(landing>=-basements&&!survivingCells.get(landing)?.has(`${x}:${z}`))landing--;
        if(landing < -basements)continue;
        const px=wx-dx*.46,pz=wz-dz*.46;
        if(px>serviceX-.2&&px<serviceX+service.width+.2&&pz>serviceZ-.2&&pz<serviceZ+service.depth+.2)continue;
        if(Math.hypot(px-entryX,pz-entryZ)<1.2)continue;
        add('household_rubble',landing,px,pz,unit*.78,.7,.22,{rotation,elevation:.035,passable:true,
            color:wealth==='poor'?'#77604a':style.color,visual_asset:'household_rubble',
            structural_debris:{version:1,material:wealth==='poor'?'wood':'masonry',seed:debrisSeed,
                source_level:source.level,source_x:wx,source_z:wz}});
    }
    if(roofType) {
        const roofing=planEditableRoofs(objects,roofBodies,{condition,seed,buildingId,group:`${buildingId}-roof`,groundHeight});
        if(roofing.error)return roofing;
        for(const {object,properties} of roofing.walls)Object.assign(object.properties,properties);
        objects.push(...roofing.objects.map(o=>({...o,properties:{...o.properties,building_generation:generation}})));
    }
    // Walls and eaves can lie on/outside the boundary, but their saved tile
    // anchor must stay inside the footprint. Offsets retain the exact geometry.
    for (const object of objects) {
        const x = object.tile_x + .5 + (object.properties.offset_x || 0);
        const z = object.tile_y + .5 + (object.properties.offset_z || 0);
        object.tile_x = Math.max(minX, Math.min(minX+width-1, object.tile_x));
        object.tile_y = Math.max(minZ, Math.min(minZ+depth-1, object.tile_y));
        object.properties.offset_x = x-object.tile_x-.5;
        object.properties.offset_z = z-object.tile_y-.5;
    }
    return { objects, cells, footprint: { minX, minZ, width, depth }, generation };
}

function compactShellObjects(objects, condition) {
    const groups = new Map();
    for (const object of objects) {
        if (!['floor', 'wall', 'roof'].includes(object.type)
            || object.type === 'roof' && !['intact', 'worn'].includes(condition)) continue;
        const { dimensions, offset_x, offset_z, ...properties } = object.properties;
        const key = JSON.stringify([object.type, dimensions.height, properties]);
        const group = groups.get(key) || [];
        group.push(object); groups.set(key, group);
    }
    const removed = new Set();
    const pose = o => ({ x:o.tile_x+.5+o.properties.offset_x, z:o.tile_y+.5+o.properties.offset_z,
        w:o.properties.dimensions.width, d:o.properties.dimensions.depth });
    for (const group of groups.values()) {
        let changed = true;
        while (changed) {
            changed = false;
            for (let i=0;i<group.length;i++) {
                const a=group[i]; if(removed.has(a))continue;
                for(let j=i+1;j<group.length;j++) {
                    const b=group[j]; if(removed.has(b))continue;
                    const p=pose(a),q=pose(b), rotated=Math.abs(a.properties.rotation||0)>.1;
                    const aw=rotated?p.d:p.w,ad=rotated?p.w:p.d,bw=rotated?q.d:q.w,bd=rotated?q.w:q.d;
                    const horizontal=Math.abs(p.z-q.z)<1e-6 && Math.abs(ad-bd)<1e-6 && Math.abs(Math.abs(p.x-q.x)-(aw+bw)/2)<1e-6;
                    const vertical=Math.abs(p.x-q.x)<1e-6 && Math.abs(aw-bw)<1e-6 && Math.abs(Math.abs(p.z-q.z)-(ad+bd)/2)<1e-6;
                    if(!horizontal&&!vertical)continue;
                    const minX=Math.min(p.x-aw/2,q.x-bw/2),maxX=Math.max(p.x+aw/2,q.x+bw/2);
                    const minZ=Math.min(p.z-ad/2,q.z-bd/2),maxZ=Math.max(p.z+ad/2,q.z+bd/2);
                    const x=(minX+maxX)/2,z=(minZ+maxZ)/2;
                    a.tile_x=Math.floor(x);a.tile_y=Math.floor(z);
                    a.properties.offset_x=x-a.tile_x-.5;a.properties.offset_z=z-a.tile_y-.5;
                    a.properties.dimensions={width:rotated?maxZ-minZ:maxX-minX,depth:rotated?maxX-minX:maxZ-minZ,height:a.properties.dimensions.height};
                    removed.add(b);changed=true;
                }
            }
        }
    }
    for(let i=objects.length-1;i>=0;i--)if(removed.has(objects[i]))objects.splice(i,1);
}

function hashSeed(seed) {
    let hash = 2166136261;
    for (const character of String(seed)) {
        hash ^= character.charCodeAt(0);
        hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
}

export function houseFootprint(startX, startZ, endX, endZ) {
    const minX = Math.min(startX, endX);
    const minZ = Math.min(startZ, endZ);
    return { minX, minZ, width: Math.abs(endX - startX) + 1, depth: Math.abs(endZ - startZ) + 1 };
}

export function houseBuildingId(locationId, footprint, seed) {
    const key = [locationId, footprint.minX, footprint.minZ,
        footprint.width, footprint.depth, seed].join(':');
    return `house-${locationId}-${hashSeed(key).toString(36)}`;
}

export function generatedFloorBlueprint(objects, buildingId, level = 0) {
    if (!buildingId || !Array.isArray(objects)) return null;
    const belongs = object => object?.properties?.building_id === buildingId
        && Number(object.properties?.level || 0) === Number(level);
    for (const wall of objects) {
        if (!belongs(wall) || wall.type !== 'wall') continue;
        const spec = wall.properties?.generated_floor_spec;
        const size = spec?.properties?.dimensions;
        if (spec?.type === 'floor' && belongs(spec) && spec.properties?.generation_seed != null
            && Number.isInteger(spec.tile_x) && Number.isInteger(spec.tile_y)
            && Number(size?.width) > 0 && Number(size?.depth) > 0 && Number(size?.height) > 0) {
            return JSON.parse(JSON.stringify(spec));
        }
    }
    return null;
}

export function restorableGeneratedFloor(objects, buildingId, level = 0) {
    if (objects?.some(object => object.type === 'floor'
        && object.properties?.building_id === buildingId
        && Number(object.properties?.level || 0) === Number(level))) return null;
    return generatedFloorBlueprint(objects, buildingId, level);
}

export function planResidentialHouse({ minX, minZ, width, depth, seed = 0, buildingId, assetById = () => null }) {
    if (!Number.isInteger(minX) || !Number.isInteger(minZ)
        || !Number.isInteger(width) || !Number.isInteger(depth)
        || width < 4 || depth < 4 || width > 12 || depth > 12 || !buildingId) {
        return { error: 'Дом: прямоугольник от 4×4 до 12×12 клеток' };
    }
    const maxX = minX + width;
    const maxZ = minZ + depth;
    const mirror = (hashSeed(seed) & 1) === 1;
    const rooms = [];
    const connections = [];
    const objects = [];
    const roomDoors = [];
    const furniture = [];
    const common = { building_id: buildingId, level: 0 };
    const makeObject = (type, name, x, z, length, thickness, height, rotation = 0, extra = {}) => ({
        name, type,
        tile_x: Math.min(maxX - 1, Math.max(minX, Math.floor(x))),
        tile_y: Math.min(maxZ - 1, Math.max(minZ, Math.floor(z))),
        properties: { ...common, dimensions: { width: length, depth: thickness, height },
            offset_x: x - Math.min(maxX - 1, Math.max(minX, Math.floor(x))) - 0.5,
            offset_z: z - Math.min(maxZ - 1, Math.max(minZ, Math.floor(z))) - 0.5,
            rotation, elevation: 0, ...extra },
    });
    const wall = (name, x, z, length, alongZ = false) => {
        objects.push(makeObject('wall', name, x, z, length, WALL_DEPTH, WALL_HEIGHT,
            alongZ ? -Math.PI / 2 : 0, { color: '#d4d0c4' }));
    };
    const door = (name, x, z, alongZ = false, from = null, to = null) => {
        objects.push(makeObject('door', name, x, z, DOOR_WIDTH, 0.18, 2,
            alongZ ? -Math.PI / 2 : 0, { color: '#665241' }));
        if (from && to) {
            connections.push([from, to]);
            roomDoors.push({ x, z, from, to });
        }
    };
    const room = (id, name, left, front, right, back) => {
        rooms.push({ id, name, bounds: { minX: left, minZ: front, maxX: right, maxZ: back } });
    };

    objects.push(makeObject('floor', 'Дом · пол', minX + width / 2, minZ + depth / 2,
        width, depth, 0.12, 0, { color: '#746b5d', roof_type: 'gable',
            generation_seed: String(seed) }));
    wall('Наружная стена', minX + width / 2, minZ, width);
    objects[1].properties.generated_floor_spec = JSON.parse(JSON.stringify(objects[0]));
    wall('Наружная стена', minX + width / 2, maxZ, width);
    wall('Наружная стена', minX, minZ + depth / 2, depth, true);
    wall('Наружная стена', maxX, minZ + depth / 2, depth, true);

    if (width >= 7 && depth >= 7 && width * depth >= 48) {
        const corridorWidth = 1.4;
        const corridorLeft = minX + (width - corridorWidth) / 2;
        const corridorRight = corridorLeft + corridorWidth;
        const splitZ = minZ + depth / 2;
        room('hall', 'Прихожая', corridorLeft, minZ, corridorRight, maxZ);
        const leftRoles = mirror ? [['bedroom', 'Спальня'], ['bathroom', 'Санузел']]
            : [['living', 'Гостиная'], ['kitchen', 'Кухня']];
        const rightRoles = mirror ? [['living', 'Гостиная'], ['kitchen', 'Кухня']]
            : [['bedroom', 'Спальня'], ['bathroom', 'Санузел']];
        for (const [side, edgeX, roles, left, right] of [
            ['left', corridorLeft, leftRoles, minX, corridorLeft],
            ['right', corridorRight, rightRoles, corridorRight, maxX],
        ]) {
            wall('Перегородка прихожей', edgeX, minZ + depth / 2, depth, true);
            wall('Межкомнатная перегородка', (left + right) / 2, splitZ, right - left);
            roles.forEach(([id, name], index) => {
                const front = index ? splitZ : minZ;
                const back = index ? maxZ : splitZ;
                room(id, name, left, front, right, back);
                door(`Дверь: ${name}`, edgeX, (front + back) / 2, true, 'hall', id);
            });
            // A window on the exterior side of each room, away from the corridor doors.
            const outerX = side === 'left' ? minX : maxX;
            for (const z of [(minZ + splitZ) / 2, (splitZ + maxZ) / 2]) {
                objects.push(makeObject('window', 'Окно', outerX, z, 1.2, 0.14, 1,
                    -Math.PI / 2, { color: '#9bb7bc', elevation: 1 }));
            }
        }
        door('Входная дверь', minX + width / 2, minZ, false, 'outside', 'hall');
    } else {
        const splitZ = minZ + (depth < 5 ? depth / 2 : Math.max(2.4, depth * 0.55));
        room('common', 'Прихожая · гостиная · кухня', minX, minZ, maxX, splitZ);
        wall('Межкомнатная перегородка', minX + width / 2, splitZ, width);
        door('Входная дверь', minX + width / 2, minZ, false, 'outside', 'common');
        if (width >= 6 && depth >= 5 && width * depth >= 30) {
            const splitX = minX + width / 2;
            const rearRoles = mirror ? [['bathroom', 'Санузел'], ['bedroom', 'Спальня']]
                : [['bedroom', 'Спальня'], ['bathroom', 'Санузел']];
            wall('Межкомнатная перегородка', splitX, (splitZ + maxZ) / 2, maxZ - splitZ, true);
            rearRoles.forEach(([id, name], index) => {
                const left = index ? splitX : minX;
                const right = index ? maxX : splitX;
                room(id, name, left, splitZ, right, maxZ);
                door(`Дверь: ${name}`, (left + right) / 2, splitZ, false, 'common', id);
                objects.push(makeObject('window', 'Окно', (left + right) / 2, maxZ,
                    1.2, 0.14, 1, 0, { color: '#9bb7bc', elevation: 1 }));
            });
        } else {
            room('bedroom', 'Спальня', minX, splitZ, maxX, maxZ);
            door('Дверь: Спальня', minX + width / 2, splitZ, false, 'common', 'bedroom');
            objects.push(makeObject('window', 'Окно', minX + width / 2, maxZ,
                1.2, 0.14, 1, 0, { color: '#9bb7bc', elevation: 1 }));
        }
    }

    const furnish = (roomId, assetId, x, z, requestedWidth = null) => {
        const bounds = rooms.find(item => item.id === roomId)?.bounds;
        const asset = assetById(assetId);
        if (!bounds || !asset || asset.id !== assetId || !Array.isArray(asset.dimensions)) return;
        const [baseWidth, furnitureDepth, furnitureHeight] = asset.dimensions;
        const furnitureWidth = requestedWidth ?? baseWidth;
        const margin = 0.25;
        if (x - furnitureWidth / 2 < bounds.minX + margin || x + furnitureWidth / 2 > bounds.maxX - margin
            || z - furnitureDepth / 2 < bounds.minZ + margin || z + furnitureDepth / 2 > bounds.maxZ - margin) return;
        if (roomDoors.some(entry => (entry.from === roomId || entry.to === roomId)
            && Math.abs(x - entry.x) < furnitureWidth / 2 + 0.75
            && Math.abs(z - entry.z) < furnitureDepth / 2 + 0.75)) return;
        if (furniture.some(entry => entry.roomId === roomId
            && Math.abs(x - entry.x) < (furnitureWidth + entry.width) / 2 + 0.1
            && Math.abs(z - entry.z) < (furnitureDepth + entry.depth) / 2 + 0.1)) return;
        objects.push(makeObject(assetId, asset.label, x, z, furnitureWidth, furnitureDepth,
            furnitureHeight, 0, { color: asset.color }));
        furniture.push({ roomId, x, z, width: furnitureWidth, depth: furnitureDepth });
    };
    for (const entry of rooms) {
        const { minX: left, minZ: front, maxX: right, maxZ: back } = entry.bounds;
        const outerLeft = left === minX;
        const outerX = outerLeft ? left : right;
        const inward = outerLeft ? 1 : -1;
        if (['common', 'living', 'kitchen'].includes(entry.id)) {
            const tableWidth = width === 4 ? 0.9 : 1.4;
            const tableX = outerX + inward * (width === 4 ? 0.8 : 0.95);
            const tableZ = front + (entry.id === 'common' ? 0.85 : 1.05);
            furnish(entry.id, 'table', tableX, tableZ, tableWidth);
            if (entry.id === 'living') furnish(entry.id, 'chair', tableX + inward * 1.15, tableZ);
        }
        if (['bedroom', 'kitchen'].includes(entry.id)) {
            furnish(entry.id, 'shelf', outerX + inward * 0.55, back - 0.5);
        }
    }

    return { rooms, connections, objects, footprint: { minX, minZ, width, depth } };
}
