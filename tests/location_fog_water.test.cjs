const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

test('туман на глубокой воде вмещает все треугольники крутого берега',async()=>{
    const terrainSource=fs.readFileSync(path.join(__dirname,'../app/static/js/locationTerrain.js'),'utf8');
    const {buildFogSurfaceData}=await import('data:text/javascript;base64,'+Buffer.from(terrainSource).toString('base64'));
    const tiles=Array.from({length:10},(_,z)=>Array.from({length:10},(_,x)=>({
        terrain:x>=2&&x<=7&&z>=2&&z<=7?'water':'grass',
        height:x>=2&&x<=7&&z>=2&&z<=7 ? .5 : 3,
    })));
    const fog=buildFogSurfaceData(tiles);
    assert.ok(fog.positions.length/3>tiles.length*tiles[0].length*24);
    const source=fs.readFileSync(path.join(__dirname,'../app/static/js/locationScene.js'),'utf8');
    const start=source.indexOf('function updateFogOverlay('),end=source.indexOf('function applyFogOfWar(',start);
    class BufferAttribute {
        constructor(array){this.array=array;}
        setUsage(){}
    }
    class Geometry {
        setAttribute(){}
        setIndex(index){this.index=index;}
        setDrawRange(start,count){this.drawRange={start,count};}
    }
    const added=[];
    const context=vm.createContext({
        currentLocationData:{tiles_data:tiles,grid_width:10,grid_height:10},
        fogSurfaceData:null,fogOverlayMeshes:new Map(),fogMemory:new Map(),
        buildFogSurfaceData,isTileVisibleToPlayer:()=>false,
        scene:{add:mesh=>added.push(mesh)},updateRoofVisibility:{lastState:null},
        THREE:{BufferGeometry:Geometry,Float32BufferAttribute:BufferAttribute,BufferAttribute,
            MeshBasicMaterial:class{},Mesh:class{constructor(geometry,material){this.geometry=geometry;this.material=material;}},
            DynamicDrawUsage:1,DoubleSide:2},
    });
    vm.runInContext(source.slice(start,end),context);
    context.updateFogOverlay(true,[]);
    const mesh=context.fogOverlayMeshes.get('unexplored');
    assert.equal(added.length,2);
    assert.equal(mesh.geometry.index.array.length,fog.positions.length/3);
    assert.equal(mesh.geometry.drawRange.count,fog.positions.length/3);
});
