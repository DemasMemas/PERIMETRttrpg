const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

test('выравнивание затрагивает только выбранный связанный водоём',()=>{
    const source=fs.readFileSync(path.join(__dirname,'../app/static/js/locationScene.js'),'utf8');
    const start=source.indexOf('export function levelLocationWaterBody(');
    const end=source.indexOf('export function applyLocationBrush(',start);
    assert.ok(start>0&&end>start);
    const tiles=[
        [{terrain:'water',height:1.4},{terrain:'water',height:.8},{terrain:'grass',height:2},{terrain:'water',height:2.5}],
        [{terrain:'water',height:1.2},{terrain:'grass',height:1},{terrain:'grass',height:1},{terrain:'water',height:2.2}],
    ];
    const updates=[],messages=[];
    const context=vm.createContext({currentLocationData:{tiles_data:tiles},window:{isGM:true},
        connectedWaterCells(getTile,x,z){
            if(getTile(x,z)?.terrain!=='water')return [];
            const cells=[[x,z]],seen=new Set([`${x},${z}`]);
            for(let i=0;i<cells.length;i++)for(const [dx,dz] of [[0,1],[1,0],[0,-1],[-1,0]]){
                const nx=cells[i][0]+dx,nz=cells[i][1]+dz,key=`${nx},${nz}`;
                if(!seen.has(key)&&getTile(nx,nz)?.terrain==='water'){seen.add(key);cells.push([nx,nz]);}
            }
            return cells;
        },
        applyLocationBrush(cells,unused,update,radius){
            updates.push({cells:cells.map(([x,z])=>[x,z]),height:update.height,radius});
            cells.forEach(([x,z])=>{tiles[z][x].height=update.height;});
        },
        showNotification:message=>messages.push(message)});
    vm.runInContext(source.slice(start,end).replace('export function','function'),context);
    assert.equal(context.levelLocationWaterBody(0,0),3);
    assert.equal(updates[0].height,.8);
    assert.equal(updates[0].radius,0);
    assert.equal(tiles[0][3].height,2.5);
    assert.equal(context.levelLocationWaterBody(3,0,'exact',1.1),2);
    assert.equal(tiles[1][3].height,1.1);
    assert.equal(context.levelLocationWaterBody(2,0),0);
    assert.equal(context.levelLocationWaterBody(0,0,'exact',4),0);
    assert.equal(updates.length,2);
    assert.equal(messages.length,2);
});
