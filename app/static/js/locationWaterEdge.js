export const SHALLOW_WATER_OFFSET = .022;

function pushTriangle(positions, a, b, c, height) {
    const cross = (b[1] - a[1]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[1] - a[1]);
    const points = cross >= 0 ? [a, b, c] : [a, c, b];
    for (const [x, z] of points) positions.push(x, height, z);
}

function pushBankTriangle(positions, colors, uvs, a, b, c, width, height, baseColors = null) {
    const cross = (b.z - a.z) * (c.x - a.x) - (b.x - a.x) * (c.z - a.z);
    for (const point of cross >= 0 ? [a, b, c] : [a, c, b]) {
        positions.push(point.x, point.y, point.z);
        colors.push(...point.color);
        uvs.push(point.x / width, point.z / height);
        if (baseColors) baseColors.push(...point.baseColor);
    }
}

const LAND_COLORS = {
    grass: [0.23, 0.37, 0.07], sand: [0.76, 0.7, 0.5],
    swamp: [0.29, 0.25, 0.18], shallow_water: [0.29, 0.25, 0.18], rock: [0.5, 0.5, 0.49],
};

const WATER_TINTS = {
    grass: [1, 1, 1],
    swamp: [0.9, 1.02, 0.91],
    sand: [1.07, 1.02, 0.92],
    rock: [0.98, 0.99, 1.02],
};

export function classifyWaterRegions(tiles, compactPools = null) {
    const regions = new Map();
    const visited = new Set();
    const directions = [[0, -1], [1, 0], [0, 1], [-1, 0]];
    for (let z = 0; z < tiles.length; z++) {
        for (let x = 0; x < tiles[z].length; x++) {
            const key = `${x},${z}`;
            if (tiles[z][x]?.terrain !== 'water' || visited.has(key)) continue;
            const cells = [];
            const shore = { grass: 0, swamp: 0, sand: 0, rock: 0 };
            const queue = [[x, z]];
            visited.add(key);
            for (let index = 0; index < queue.length; index++) {
                const [cx, cz] = queue[index];
                cells.push([cx, cz]);
                for (const [dx, dz] of directions) {
                    const nx = cx + dx;
                    const nz = cz + dz;
                    const neighbor = tiles[nz]?.[nx];
                    if (!neighbor) continue;
                    if (neighbor.terrain === 'water') {
                        const neighborKey = `${nx},${nz}`;
                        if (!visited.has(neighborKey)) {
                            visited.add(neighborKey);
                            queue.push([nx, nz]);
                        }
                    } else {
                        const kind = neighbor.terrain === 'shallow_water' ? 'swamp' : neighbor.terrain;
                        if (shore[kind] !== undefined) shore[kind]++;
                    }
                }
            }
            const total = Object.values(shore).reduce((sum, count) => sum + count, 0);
            const [dominant, count] = Object.entries(shore)
                .sort((a, b) => b[1] - a[1])[0];
            const kind = total && count / total >= 0.55 ? dominant : 'grass';
            const compact = compactPools && cells.length <= 6
                && !cells.some(([cx, cz]) => directions.some(([dx, dz]) =>
                    tiles[cz + dz]?.[cx + dx]?.terrain === 'shallow_water'));
            for (const [cx, cz] of cells) regions.set(`${cx},${cz}`, kind);
            if (compact) for (const [cx, cz] of cells) compactPools.add(`${cx},${cz}`);
        }
    }
    return regions;
}

export function waterLevelForTile(tile, kind) {
    const offset=Number.isFinite(tile.water_level_offset)?tile.water_level_offset:(kind==='sand'?-.2:0);
    return (Number(tile.height) || 1) + offset;
}

export function waterExposedSideMask(tiles, x, z, outsideOnly = false) {
    if (tiles[z]?.[x]?.terrain !== 'water') return 0;
    const exposed = (dx, dz) => outsideOnly ? !tiles[z+dz]?.[x+dx] : tiles[z + dz]?.[x + dx]?.terrain !== 'water';
    return (exposed(1, 0) ? 1 : 0) | (exposed(-1, 0) ? 2 : 0)
        | (exposed(0, 1) ? 4 : 0) | (exposed(0, -1) ? 8 : 0);
}

export function connectedWaterHeightAt(tiles, x, z, px, pz, regions,
    levelForWater = waterLevelForTile) {
    const xEdge = Math.round(px);
    const zEdge = Math.round(pz);
    const xs = Math.abs(px - xEdge) < 1e-8 ? [xEdge - 1, xEdge] : [x];
    const zs = Math.abs(pz - zEdge) < 1e-8 ? [zEdge - 1, zEdge] : [z];
    const touching = new Map();
    for (const tz of zs) for (const tx of xs) {
        if (tiles[tz]?.[tx]?.terrain === 'water') touching.set(`${tx},${tz}`, [tx, tz]);
    }
    const connected = new Set([`${x},${z}`]);
    const queue = [[x, z]];
    for (const [cx, cz] of queue) {
        for (const [dx, dz] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
            const key = `${cx + dx},${cz + dz}`;
            if (!touching.has(key) || connected.has(key)) continue;
            connected.add(key);
            queue.push(touching.get(key));
        }
    }
    let total = 0;
    for (const key of connected) {
        const [tx, tz] = touching.get(key);
        total += levelForWater(tiles[tz][tx], regions.get(key) || 'grass');
    }
    return total / connected.size;
}

export function waterSideCornerDrops(tiles, x, z, regions, renderedHeight) {
    const drop = (px, pz) => {
        const waterTop = connectedWaterHeightAt(tiles, x, z, px, pz, regions) + 0.012;
        return (renderedHeight - Math.max(0, waterTop)) / renderedHeight;
    };
    return [drop(x, z), drop(x + 1, z), drop(x + 1, z + 1), drop(x, z + 1)];
}

export function buildRoundedWaterData(tiles, radius = 0.24,
    includeTile = () => true, bankSamples = null, levelForWater = waterLevelForTile,
    { shorelineRing = false, landSurfaceHeightAt = null, worldOrigin = null, flatShore = false,
        captureFog = false } = {}) {
    if (shorelineRing) return buildShoreSurface(tiles, includeTile, levelForWater,
        landSurfaceHeightAt, worldOrigin, captureFog);
    const water = [];
    const waterColors = [];
    const bank = [];
    const bankColors = [];
    const bankUvs = [];
    const bankBaseColors = [];
    const sandBank = [];
    const sandBankColors = [];
    const sandBankUvs = [];
    // The world shore is local to the adjoining land, never a truncated basin vote.
    // Keep the established local-location classification and geometry unchanged.
    const regions = worldOrigin ? new Map() : classifyWaterRegions(tiles);
    const isLand = (x, z) => Boolean(tiles[z]?.[x] && tiles[z][x].terrain !== 'water');
    // Sample from touching land tiles so adjacent water cells agree on their shared shore edge.
    const sharedShoreHeight = (px, pz) => {
        const xEdge = Math.round(px);
        const zEdge = Math.round(pz);
        const xs = Math.abs(px - xEdge) < 1e-8 ? [xEdge - 1, xEdge] : [Math.floor(px)];
        const zs = Math.abs(pz - zEdge) < 1e-8 ? [zEdge - 1, zEdge] : [Math.floor(pz)];
        const heights = [];
        for (const landZ of zs) {
            for (const landX of xs) {
                if (!isLand(landX, landZ)) continue;
                const value = landSurfaceHeightAt
                    ? landSurfaceHeightAt(
                        Math.max(landX + 0.00001, Math.min(landX + 0.99999, px)),
                        Math.max(landZ + 0.00001, Math.min(landZ + 0.99999, pz)))
                    : Number(tiles[landZ][landX].height);
                if (Number.isFinite(value)) heights.push(value);
            }
        }
        return heights.length ? heights.reduce((sum, value) => sum + value, 0) / heights.length : null;
    };
    for (let z = 0; z < tiles.length; z++) {
        for (let x = 0; x < tiles[z].length; x++) {
            const tile = tiles[z][x];
            if (tile?.terrain !== 'water' || !includeTile(x, z)) continue;
            const kind = regions.get(`${x},${z}`) || 'grass';
            const height = levelForWater(tile, kind);
            const patternX = x + (worldOrigin?.x || 0), patternZ = z + (worldOrigin?.z || 0);
            const rawWaterHeightAt = (px, pz) => shorelineRing
                ? connectedWaterHeightAt(tiles, x, z, px, pz, regions, levelForWater) : height;
            const waterHeightAt = (px,pz) => {
                const level=rawWaterHeightAt(px,pz),land=flatShore?sharedShoreHeight(px,pz):null;
                // End a level water boundary on the real land edge. Keeping the
                // usual raised water edge here would leave an open .012 lip.
                return land!==null&&Math.abs(land-level)<.0001 ? land-.012 : level;
            };
            const strip = 0.075;
            const r = Math.max(strip * 2, Math.min(radius, 0.45));
            const diagonal = (r - strip) * Math.SQRT1_2;
            // A level land/water seam does not need an inset dirt rim. Decide
            // from the actual edge, not a basin-wide or chunk-dependent vote.
            const bankNeeded=(nx,nz,px,pz)=>isLand(nx,nz)&&(!flatShore
                ||Math.abs((sharedShoreHeight(px,pz)??height)-rawWaterHeightAt(px,pz))>0.0001);
            const north = bankNeeded(x,z-1,x+.5,z);
            const east = bankNeeded(x+1,z,x+1,z+.5);
            const south = bankNeeded(x,z+1,x+.5,z+1);
            const west = bankNeeded(x-1,z,x,z+.5);
            const corners = [
                {
                    first: west, second: north, at: [x, z], diagonal: [x - 1, z - 1],
                    firstOnly: [x + strip, z], secondOnly: [x, z + strip], diagonalLand: bankNeeded(x-1,z-1,x,z),
                    arc: [[x + strip, z + r], [x + r - diagonal, z + r - diagonal], [x + r, z + strip]],
                    edge: [x + 0.5, z + (north ? strip + 0.025 * Math.sin(patternX * 4.3 + patternZ * 1.7) : 0)],
                },
                {
                    first: north, second: east, at: [x + 1, z], diagonal: [x + 1, z - 1],
                    firstOnly: [x + 1, z + strip], secondOnly: [x + 1 - strip, z], diagonalLand: bankNeeded(x+1,z-1,x+1,z),
                    arc: [[x + 1 - r, z + strip], [x + 1 - r + diagonal, z + r - diagonal], [x + 1 - strip, z + r]],
                    edge: [x + 1 - (east ? strip + 0.025 * Math.sin(patternX * 3.1 + patternZ * 5.1) : 0), z + 0.5],
                },
                {
                    first: east, second: south, at: [x + 1, z + 1], diagonal: [x + 1, z + 1],
                    firstOnly: [x + 1 - strip, z + 1], secondOnly: [x + 1, z + 1 - strip], diagonalLand: bankNeeded(x+1,z+1,x+1,z+1),
                    arc: [[x + 1 - strip, z + 1 - r], [x + 1 - r + diagonal, z + 1 - r + diagonal], [x + 1 - r, z + 1 - strip]],
                    edge: [x + 0.5, z + 1 - (south ? strip + 0.025 * Math.sin(patternX * 4.3 + (patternZ + 1) * 1.7) : 0)],
                },
                {
                    first: south, second: west, at: [x, z + 1], diagonal: [x - 1, z + 1],
                    firstOnly: [x, z + 1 - strip], secondOnly: [x + strip, z + 1], diagonalLand: bankNeeded(x-1,z+1,x,z+1),
                    arc: [[x + r, z + 1 - strip], [x + r - diagonal, z + 1 - r + diagonal], [x + strip, z + 1 - r]],
                    edge: [x + (west ? strip + 0.025 * Math.sin((patternX - 1) * 3.1 + patternZ * 5.1) : 0), z + 0.5],
                },
            ];
            const outline = [];
            const waterOutline = [];
                    for (const corner of corners) {
                let points;
                let diagonalOnly = false;
                const diagonalWater = false;
                if (corner.first && corner.second) points = corner.arc;
                else if (corner.first && (corner.diagonalLand || diagonalWater)) {
                    points = [corner.firstOnly];
                } else if (corner.second && (corner.diagonalLand || diagonalWater)) {
                    points = [corner.secondOnly];
                } else points = [corner.at];
                outline.push(...points);
                if (!diagonalOnly) waterOutline.push(...points);
                outline.push(corner.edge);
                waterOutline.push(corner.edge);
            }
            const shore = [
                { x, z: z - 1, distance: (u, v) => v },
                { x: x + 1, z, distance: (u) => 1 - u },
                { x, z: z + 1, distance: (u, v) => 1 - v },
                { x: x - 1, z, distance: (u) => u },
            ].filter(side => isLand(side.x, side.z));
            if (shore.length) {
                const bankPoint = (u, v) => {
                    let nearest = 1;
                    let weightTotal = 0;
                    let landHeight = 0;
                    const landColor = [0, 0, 0];
                    for (const side of shore) {
                        const distance = side.distance(u, v);
                        nearest = Math.min(nearest, distance);
                        const weight = 1 / (0.12 + distance) ** 3;
                        const land = tiles[side.z][side.x];
                        weightTotal += weight;
                        landHeight += (Number(land.height) || 1) * weight;
                        const tint = LAND_COLORS[land.terrain] || LAND_COLORS.grass;
                        for (let channel = 0; channel < 3; channel++) {
                            landColor[channel] += tint[channel] * weight;
                        }
                    }
                    const rise = Math.min(1, nearest / (kind === 'sand' ? 0.23 : 0.05));
                    const smoothRise = rise * rise * (3 - 2 * rise);
                    const ripple = Math.sin((x + u) * 19.3 + Math.cos((z + v) * 13.7)) * 0.018;
                    const wetShade = Math.max(0, Math.min(1,
                        (nearest + ripple) / (kind === 'sand' ? 0.31 : 0.19)));
                    return {
                        x: x + u,
                        y: landHeight / weightTotal * (1 - smoothRise) + height * smoothRise + 0.006,
                        z: z + v,
                        color: kind === 'sand'
                            ? [0.83 - 0.22 * wetShade, 0.73 - 0.2 * wetShade, 0.47 - 0.12 * wetShade]
                            : [1 - 0.27 * wetShade, 1 - 0.15 * wetShade, 1 - 0.13 * wetShade],
                        baseColor: landColor.map(value => value / weightTotal * (1 - 0.2 * wetShade)),
                    };
                };
                const target = kind === 'sand'
                    ? [sandBank, sandBankColors, sandBankUvs] : [bank, bankColors, bankUvs];
                const baseColors = kind === 'sand' ? null : bankBaseColors;

                const samples = bankSamples || [0, 0.05, 0.075, 0.125, 0.25, 0.375, 0.5,
                    0.625, 0.75, 0.875, 0.925, 0.95, 1];
                const vertices = samples.map(v => samples.map(u => bankPoint(u, v)));
                for (let row = 0; row < samples.length - 1; row++) {
                    for (let column = 0; column < samples.length - 1; column++) {
                        const a = vertices[row][column];
                        const b = vertices[row][column + 1];
                        const c = vertices[row + 1][column + 1];
                        const d = vertices[row + 1][column];
                        pushBankTriangle(...target, a, b, c, tiles[z].length, tiles.length, baseColors);
                        pushBankTriangle(...target, a, c, d, tiles[z].length, tiles.length, baseColors);
                    }
                }
            }

            const center = [x + 0.5, z + 0.5];
            for (let index = 0; index < waterOutline.length; index++) {

                pushTriangle(water, center, waterOutline[index],
                    waterOutline[(index + 1) % waterOutline.length], height + 0.012);

                for (let vertex = 0; vertex < 3; vertex++) waterColors.push(...WATER_TINTS[kind]);
            }
        }
    }
    return { water, waterColors, bank, bankColors, bankUvs, bankBaseColors,
        sandBank, sandBankColors, sandBankUvs, regions };
}

// One conforming surface per water cell. A shared lattice and a continuous
// distance-to-land field replace the independently projected radial rings.
// Clip each triangle once: its water and bank polygons partition the footprint,
// including concave corners and diagonally disconnected bodies of water.
function buildShoreSurface(tiles, includeTile, levelForWater, sampleLand, origin, captureFog) {
    const compactPools = new Set();
    const data={water:[],waterColors:[],bank:[],bankColors:[],bankUvs:[],bankBaseColors:[],
        sandBank:[],sandBankColors:[],sandBankUvs:[],bankShoulders:[],sandBankShoulders:[],
        fogByTile:[],waterMargins:[],compactPools,
        regions:origin?new Map():classifyWaterRegions(tiles, compactPools)};
    const smooth=t=>t*t*(3-2*t);
    for(let z=0;z<tiles.length;z++)for(let x=0;x<(tiles[z]?.length||0);x++){
        if(tiles[z][x]?.terrain!=='water'||!includeTile(x,z))continue;
        const surfaceStarts=captureFog?[data.water.length,data.bank.length,data.sandBank.length]:null;
        const saveFogSurface=()=>{
            if(!captureFog)return;
            data.fogByTile[z*tiles[z].length+x]=[
                ...data.water.slice(surfaceStarts[0]),
                ...data.bank.slice(surfaceStarts[1]),
                ...data.sandBank.slice(surfaceStarts[2]),
            ];
        };
        const kind=data.regions.get(`${x},${z}`)||'grass',level=levelForWater(tiles[z][x],kind),land=[];
        for(let dz=-1;dz<=1;dz++)for(let dx=-1;dx<=1;dx++){
            const tile=tiles[z+dz]?.[x+dx];
            if(tile&&tile.terrain!=='water')land.push({x:x+dx,z:z+dz,tile});
        }
        const compact = compactPools.has(`${x},${z}`);
        // Мелким водоёмам нужен более подробный контур на расстоянии игровой камеры.
        const samples = compact ? [0,.075,.185,.34,.5,.66,.815,.925,1] : [0,.25,.5,.75,1];
        const steps = samples.length - 1;
        const ring=[[0,0],[.5,0],[1,0],[1,.5],[1,1],[.5,1],[0,1],[0,.5]];
        const heights=ring.map(([u,v])=>connectedWaterHeightAt(tiles,x,z,x+u,z+v,data.regions,levelForWater));
        const waterHeight=(u,v)=>{
            const dx=u-.5,dz=v-.5,r=Math.max(Math.abs(dx),Math.abs(dz))*2;
            if(r<1e-9)return level+.012;
            const a=Math.atan2(dz,dx),side=((Math.floor((a+Math.PI*3/4)/(Math.PI/4))+8)%8);
            // Interpolate on the boundary of the original eight-triangle fan.
            const [ax,az]=ring[side], [bx,bz]=ring[(side+1)%8];
            const px=.5+dx/r,pz=.5+dz/r;
            const t=Math.abs(bx-ax)>1e-9?(px-ax)/(bx-ax):(pz-az)/(bz-az);
            return level+(heights[side]*(1-t)+heights[(side+1)%8]*t-level)*r+.012;
        };
        const point=(u,v)=>{
            const px=x+u,pz=z+v,waterY=waterHeight(u,v);
            const wx=px+(origin?.x||0),wz=pz+(origin?.z||0);
            let total=0,target=0,red=0,green=0,blue=0,nearest=Infinity,shallow=0;
            let shallowTarget=0, shallowDistance=Infinity;
            const touching=[];
            const distances=[1,1,1,1];
            for(const cell of land){
                const lx=Math.max(cell.x,Math.min(cell.x+1,px)),lz=Math.max(cell.z,Math.min(cell.z+1,pz)),distance=Math.hypot(px-lx,pz-lz);
                const ground=sampleLand?sampleLand(Math.max(cell.x+1e-5,Math.min(cell.x+.99999,lx)),Math.max(cell.z+1e-5,Math.min(cell.z+.99999,lz))):Number(cell.tile.height);
                nearest=Math.min(nearest,distance);
                if(distance<1e-8)touching.push(ground);
                const wetBank=cell.tile.terrain==='swamp'||cell.tile.terrain==='shallow_water';
                const relief = Math.abs(ground-(waterY-.012));
                const width = compact
                    ? .238 + Math.min(.095,relief*.12) + .041*Math.sin(wx*5.3+wz*2.8) + .027*Math.sin(wz*7.1-wx*3.2)
                    : (wetBank?.16:.25) + Math.min(.07,relief*.04)
                        + .025*Math.sin(wx*.73+wz*.41) + .018*Math.sin(wz*1.13-wx*.28);
                const direction=Math.abs(px-lx)>Math.abs(pz-lz)+1e-9?(px<lx?1:3):(pz<lz?2:0);
                distances[direction]=Math.min(distances[direction],distance-width);
                if(distance>.75)continue;
                const weight=1/(.015+distance)**4,tint=LAND_COLORS[cell.tile.terrain]||LAND_COLORS.grass;
                total+=weight;target+=ground*weight;red+=tint[0]*weight;green+=tint[1]*weight;blue+=tint[2]*weight;
                if(cell.tile.terrain==='shallow_water'){
                    shallow+=weight;
                    shallowTarget+=ground*weight;
                    shallowDistance=Math.min(shallowDistance,distance);
                }
            }
            const soften=compact?.18:land.every(c=>['swamp','shallow_water'].includes(c.tile.terrain))?.12:.28;
            let distance=distances[0];
            for(const next of distances.slice(1)){
                const h=Math.max(0,soften-Math.abs(distance-next))/soften;
                distance=Math.min(distance,next)-h*h*soften*.25;
            }
            const mask=-distance;
            // Normalise against actual distance to land. The former fixed .22
            // denominator left narrow swamp banks short of the ground edge.
            const bankT = mask > 0 ? mask / (mask + nearest) : 0;
            const blend = smooth(compact ? Math.min(1, bankT * 1.13) : bankT);
            const ground=touching.length?touching.reduce((a,b)=>a+b,0)/touching.length:total?target/total:waterY;
            // Поверхность плавно поднимается к мелководью ещё в пределах глубокой клетки.
            const shoal = shallow ? smooth(Math.max(0, 1 - shallowDistance/.75)) : 0;
            const level = shoal ? waterY + (shallowTarget/shallow+SHALLOW_WATER_OFFSET-waterY)*shoal : waterY;
            return {x:px,z:pz,y:level*(1-blend)+(ground+.002)*blend,
                waterY:level,mask,blend,shoulder:compact ? smooth(Math.max(0,1-nearest/.22)) : 0,
                shallow:total?shallow/total:0,color:[1,1,1],
                baseColor:total?[red/total,green/total,blue/total]:[1,1,1]};
        };
        const clip=(polygon,bank)=>{
            const out=[];let a=polygon.at(-1),inside=bank?a.mask>=0:a.mask<=0;
            for(const b of polygon){
                const next=bank?b.mask>=0:b.mask<=0;
                if(next!==inside){
                    const t=a.mask/(a.mask-b.mask),p={mask:0,blend:0,shoulder:0,color:[1,1,1]};
                    for(const key of ['x','z','waterY','shallow'])p[key]=a[key]+(b[key]-a[key])*t;
                    p.y=p.waterY;p.baseColor=a.baseColor.map((v,i)=>v+(b.baseColor[i]-v)*t);out.push(p);
                }
                if(next)out.push(b);a=b;inside=next;
            }
            return out;
        };
        const emit=(polygon,bank)=>{
            for(let i=1;i<polygon.length-1;i++){
                const points=[polygon[0],polygon[i],polygon[i+1]], [a,b,c]=points;
                const cross=(b.z-a.z)*(c.x-a.x)-(b.x-a.x)*(c.z-a.z);
                if(Math.abs(cross)<1e-10)continue;
                const submerged = bank && points.some(point => point.shallow > .66);
                if(bank && !submerged){
                    const sand=kind==='sand';
                    pushBankTriangle(sand?data.sandBank:data.bank,sand?data.sandBankColors:data.bankColors,
                        sand?data.sandBankUvs:data.bankUvs,a,b,c,tiles[z].length,tiles.length,sand?null:data.bankBaseColors);
                    if (!origin) (sand ? data.sandBankShoulders : data.bankShoulders)
                        .push(...(cross >= 0 ? points : [a,c,b]).map(p => p.shoulder));
                }else for(const p of cross>=0?points:[a,c,b]){
                    const y = submerged ? p.y + (SHALLOW_WATER_OFFSET - .002) * p.blend : p.waterY;
                    data.water.push(p.x,y,p.z);data.waterColors.push(...WATER_TINTS[kind]);
                    if (!origin) data.waterMargins.push(submerged || p.shallow > .45 ? 1
                        : Math.min(1, Math.max(0, -p.mask) / (compact ? .145 : .19)));
                }
            }
        };
        if(!land.length){
            const center=point(.5,.5),outline=[];
            const edges=origin?2:steps;
            for(let i=0;i<edges;i++)outline.push(point(i/edges,0));
            for(let i=0;i<edges;i++)outline.push(point(1,i/edges));
            for(let i=0;i<edges;i++)outline.push(point(1-i/edges,1));
            for(let i=0;i<edges;i++)outline.push(point(0,1-i/edges));
            for(let i=0;i<outline.length;i++)emit([center,outline[i],outline[(i+1)%outline.length]],false);
            saveFogSurface();
            continue;
        }
        const grid=samples.map(v=>samples.map(u=>point(u,v)));
        for(let row=0;row<steps;row++)for(let col=0;col<steps;col++){
            const a=grid[row][col],b=grid[row][col+1],c=grid[row+1][col+1],d=grid[row+1][col];
            for(const triangle of [[a,b,c],[a,c,d]]){emit(clip(triangle,false),false);emit(clip(triangle,true),true);}
        }
        saveFogSurface();
    }
    return data;
}
