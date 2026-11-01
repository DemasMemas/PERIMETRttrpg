// Ошибки навигации из игровых снимков: проверка на той же полной Припяти.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,peer,request,token,lobby,safeSession,location,actor,player})=>{
    const url=`/lobbies/${lobby.id}/locations/${location.id}`;
    const results=[];
    const profileParity=await page.evaluate(`(async()=>{
        const {createHouseholdModel}=await import('/static/js/locationHouseholdModels.js');
        const {findLocationAsset}=await import('/static/js/locationAssetCatalog.js');
        const {profiles}=await (await fetch('/static/data/treeMovementProfiles.json')).json();
        for(const [id,expected]of Object.entries(profiles)){
            const mesh=createHouseholdModel(T,findLocationAsset('nature_'+id),{width:1,depth:1,height:1});
            const actual=mesh.geometry.userData.movementTrunk;
            if(!actual||['min','max'].some(key=>actual[key].some((v,i)=>Math.abs(v-expected[key][i])>1e-6)))throw Error('Контур ствола отличается от модели: '+id);
            mesh.material.dispose();
        }
        return Object.keys(profiles).length;
    })()`);
    assert.equal(profileParity,9);
    const place=async(x,y,level=0)=>{
        await request(url+'/spawn_character',{character_id:actor.id,tile_x:x,tile_y:y,floor_level:level,assign_to_user_id:player.user_id},token);
        for(const c of [page,peer])await c.until(`(()=>{const p=L.characterPose(${actor.id}),s=L.characterSpatialPose(${actor.id});return Math.abs(p.x-${x})<1e-8&&Math.abs(p.y-${y})<1e-8&&s.level===${level}&&!s.animating})()`,'исходная позиция навигации');
    };
    const preview=async(x,y,level)=>{
        const t=performance.now();const response=await request(url+'/movement-preview',{character_id:actor.id,x,y,floor_level:level},token);
        results.push({target:[x,y,level],responseMs:Math.round(performance.now()-t),route:response.route});
        assert.ok(response.route,`путь к ${x},${y}, этаж ${level}`);return response.route;
    };
    const move=async(x,y,level,extra={})=>{
        await peer.evaluate(`void socket.emit('move_in_location',{token:localStorage.getItem('access_token'),location_id:${location.id},character_id:${actor.id},x:${x},y:${y},floor_level:${level},movement_mode:'walk',...${JSON.stringify(extra)}})`);
        for(const c of [page,peer])await c.until(`(()=>{const p=L.characterPose(${actor.id}),s=L.characterSpatialPose(${actor.id});return Math.abs(p.x-${x})<1e-8&&Math.abs(p.y-${y})<1e-8&&s.level===${level}&&!s.animating})()`,'переход у двух клиентов');
    };
    await place(31,46);const tree=await preview(36,46,0);
    assert.ok(tree.distance>5||tree.climbs.length,'дерево обходится или оплачивается перелезание');
    await move(36,46,0);
    await place(64,29);const road=await preview(64,46,0);
    assert.ok(road.distance>17,'фургон обходится');
    await move(64,46,0);
    await preview(64,71,0);await move(64,71,0);
    await preview(64,0,0);await move(64,0,0);
    // Дробная цель курсора округляется до 0,1 м и действительно исполняется.
    await place(64,68,0);
    await page.call('Page.bringToFront');
    await page.evaluate(`L.setLocationEditMode(false);L.setLocationViewLevel(0);locationControls.object.position.set(58,14,79);locationControls.target.set(65,1,69);locationControls.update();locationControls.object.updateMatrixWorld(true);L.beginCharacterMoveMode(${actor.id},'walk')`);
    const edgePointer=await page.evaluate(`(()=>{const v=new T.Vector3(64.934,1,71.48).project(locationControls.object),r=document.querySelector('#location-container canvas').getBoundingClientRect();return{x:r.left+(v.x+1)*r.width/2,y:r.top+(1-v.y)*r.height/2}})()`);
    await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',...edgePointer});
    await page.evaluate(`L.updateMovementPreview(${edgePointer.x},${edgePointer.y})`);
    await page.until(`L.navigationPreviewStateForTest().route?.path?.at(-1)?.[0]===64.4&&L.navigationPreviewStateForTest().route?.path?.at(-1)?.[1]===71`,'округлённая цель у края под кроной');
    const edgeHint=await page.evaluate('L.movementHintText()');
    assert.ok(!/\d+\.\d{2,}/.test(edgeHint),edgeHint);
    fs.writeFileSync(path.join(safeSession.artifacts,'tree-edge-tenths.png'),Buffer.from((await page.evaluate('L.captureCurrentSceneForTest()')).split(',')[1],'base64'));
    await page.evaluate(`void L.commitMovementPreview(${edgePointer.x},${edgePointer.y})`);
    for(const c of [page,peer])await c.until(`(()=>{const p=L.characterPose(${actor.id}),s=L.characterSpatialPose(${actor.id});return p.x===64.4&&p.y===71&&!s.animating})()`,'десятые метра сохранены у двух клиентов');
    results.push({kind:'tree-edge',profileParity,edgeHint,position:[64.4,71]});
    await place(25,11,1);await preview(25,3,0);
    await page.call('Page.bringToFront');
    await page.evaluate(`L.setLocationEditMode(false);L.setLocationViewLevel(1);locationControls.object.position.set(25.5,28,-7);locationControls.target.set(25.5,2,8);locationControls.update();locationControls.object.updateMatrixWorld(true);L.beginCharacterMoveMode(${actor.id},'walk')`);
    const pointer=await page.evaluate(`(()=>{const v=new T.Vector3(25.5,1,3.5).project(locationControls.object),r=document.querySelector('#location-container canvas').getBoundingClientRect();return {x:r.left+(v.x+1)*r.width/2,y:r.top+(1-v.y)*r.height/2}})()`);
    await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',x:pointer.x,y:pointer.y});
    await page.evaluate(`L.updateMovementPreview(${pointer.x},${pointer.y})`);
    await page.until('L.navigationPreviewStateForTest().route?.surfaces?.length>1','предпросмотр со второго этажа наружу');
    const pointerState=await page.evaluate('L.navigationPreviewStateForTest()');
    assert.equal(pointerState.level,0,'указатель на улице выбирает землю');
    assert.ok(Math.max(...pointerState.points.map(p=>p[1]))>3,'линия проходит по верхнему этажу');
    assert.ok(Math.min(...pointerState.points.map(p=>p[1]))<2,'линия спускается на улицу');
    assert.equal(pointerState.frustumCulled,false);
    for(const [i,camera] of [[0,[16,15,4]],[1,[36,7,18]],[2,[25,35,5]]]){
        await page.evaluate(`locationControls.object.position.set(...${JSON.stringify(camera)});locationControls.target.set(25.5,2,8);locationControls.update();locationControls.object.updateMatrixWorld(true)`);
        fs.writeFileSync(path.join(safeSession.artifacts,`route-angle-${i}.png`),Buffer.from((await page.evaluate('L.captureCurrentSceneForTest()')).split(',')[1],'base64'));
    }
    await move(25,3,0);
    await place(25,7,0);await preview(25,3,0);
    await place(27,6,1);
    const full=await request(url,null,token,'GET');
    const opening=full.objects.find(o=>o.type==='floor_opening'&&o.tile_x===29&&o.tile_y===6&&o.properties?.level===1);
    assert.ok(opening,'сохранённый проём здания с двумя Банями и Вспышкой');
    const drop=await request(url+'/movement-preview',{character_id:actor.id,special_action:'drop_down',object_id:opening.id},token);
    assert.ok(drop.route,'спрыгивание через край реального проёма');
    // Вызывается тот же обработчик, что у кнопки «Спрыгнуть вниз»: он сам
    // запрашивает серверную точку, а не получает координаты от теста.
    assert.equal(await peer.evaluate(`L.useLocationVerticalAccess(${opening.id},'drop',${actor.id})`),true);
    for(const c of [page,peer])await c.until(`(()=>{const p=L.characterPose(${actor.id}),s=L.characterSpatialPose(${actor.id});return Math.abs(p.x-${drop.route.landing.x})<1e-8&&Math.abs(p.y-${drop.route.landing.y})<1e-8&&s.level===0&&!s.animating})()`,'спрыгивание кнопкой у двух клиентов');
    results.push({kind:'drop',route:drop.route,twoClients:true});
    fs.writeFileSync(path.join(safeSession.artifacts,'reported-navigation.json'),JSON.stringify({scene:'Припять 72×72',building:'pripyat-1',pointerState,results},null,2));
    console.log('PASS: дерево, дорога с фургоном, выход из pripyat-1 со второго этажа, высота линии и проём у двух клиентов');
};
