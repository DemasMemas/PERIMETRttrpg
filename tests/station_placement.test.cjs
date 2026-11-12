const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('app/static/js/locationScene.js','utf8');
function setup(send){
    const requests=[],added=[],disposed=[];
    const scene={add(ghost){ghost.parent=this;},remove(){}};
    const context=vm.createContext({currentLocationData:{},currentLocationId:5,pendingStationPlacement:null,
        combatState:{status:'idle'},window:Object.assign(new EventTarget(),{currentLobbyId:2}),AbortController,scene,crypto:{randomUUID:()=> 'station-operation'},
        getLocationCharacterById:()=>({floorLevel:0}),canControlCharacter:()=>true,
        findLocationAsset:()=>({id:'station_electrician',baseType:'household_workbench',dimensions:[1.6,.7,.9]}),
        placementForLocationAsset:asset=>({type:asset.baseType,visualAsset:asset.id}),
        clearPendingCombatAction(){},hideStructureInteraction(){},closeMapEditorQuickMenu(){},showNotification(){},
        createLocationObjectMesh:()=>({traverse(){},position:{copy(point){this.x=point.x;this.z=point.z;}},rotation:{}}),
        disposeObject:ghost=>disposed.push(ghost),point:{x:5.7,z:4.5,floor_level:0},
        localMapToolPoint(){return context.point;},
        addLocationObject:object=>added.push(object),updateLocationObject:object=>added.push(object),
        Server:{async operateStation(lobby,location,payload){requests.push(payload);return send?send(context,payload):{created:true,object:{id:9}};}},
    });
    for(const name of ['clearStationPlacement','beginStationPlacement','updateStationPreview','commitStationPlacement']){
        const code=source.match(new RegExp('(?:export )?(?:async )?function '+name+'\\([\\s\\S]*?\\n\\}'))?.[0];
        assert.ok(code,name);vm.runInContext(code.replace(/^export /,''),context);
    }
    context.beginStationPlacement({characterId:4,revision:7,itemPath:['inventory','pockets',0],itemId:'station-item',assetId:'station_electrician'});
    return {context,requests,added,disposed};
}

test('предпросмотр и отмена не списывают предмет и не создают объект',()=>{
    const {context:c,requests,disposed}=setup();
    c.updateStationPreview(1,1);assert.equal(c.pendingStationPlacement.ghost.visible,true);
    c.point={x:5,z:5,floor_level:1};c.updateStationPreview(1,1);
    assert.equal(c.pendingStationPlacement.ghost.visible,false);
    const signal=c.pendingStationPlacement.keys.signal;
    c.clearStationPlacement();assert.equal(c.pendingStationPlacement,null);assert.equal(signal.aborted,true);
    assert.equal(requests.length,0);assert.equal(disposed.length,1);
});

test('подтверждение передаёт выбранную точку и ревизию сервера, затем очищает предпросмотр',async()=>{
    const {context:c,requests,added,disposed}=setup();
    await c.commitStationPlacement(1,1);
    assert.equal(requests[0].x,5.2);assert.equal(requests[0].y,4);
    assert.equal(requests[0].character_revision,7);assert.equal(requests[0].item_id,'station-item');
    assert.equal(added.length,1);assert.equal(disposed.length,1);assert.equal(c.pendingStationPlacement,null);
});

test('повтор после обрыва сети использует тот же запрос без нового списания',async()=>{
    let count=0;const {context:c,requests,added}=setup(()=>{if(!count++)throw new Error('network');return {created:true,object:{id:9}};});
    await c.commitStationPlacement(1,1);assert.ok(c.pendingStationPlacement);
    c.point={x:10,z:10,floor_level:0};c.updateStationPreview(2,2);
    await c.commitStationPlacement(2,2);
    assert.equal(requests[0],requests[1]);assert.equal(requests[1].x,5.2);assert.equal(added.length,1);
});

test('запоздавший ответ не добавляет устройство в другую открытую подлокацию',async()=>{
    const {context:c,added}=setup(context=>{context.currentLocationId=6;return {created:true,object:{id:9}};});
    await c.commitStationPlacement(1,1);assert.equal(added.length,0);
});
