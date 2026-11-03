const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');

module.exports=async({page,peer,request,token,lobby,location,actor,player,safeSession,click})=>{
    const url='/lobbies/'+lobby.id+'/locations/'+location.id,sheetUrl='/lobbies/characters/'+actor.id;
    const detail=await request(url,null,token,'GET');
    const wall=detail.objects.find(object=>object.type==='wall'&&object.tile_x===57&&object.tile_y===10&&object.properties?.visual_asset==='inner_wall');
    assert.ok(wall,'настоящая сломанная внутренняя перегородка Припяти');
    const pose=async(x,y)=>{
        for(const client of [page,peer])await client.until('(()=>{const p=L.characterPose('+actor.id+'),s=L.characterSpatialPose('+actor.id+');return p&&p.x==='+x+'&&p.y==='+y+'&&!s.animating})()','одинаковая конечная клетка у двух клиентов');
    };
    const setup=async()=>{
        const state=await request(url+'/combat',null,token,'GET');
        if(state.status==='active')await request(url+'/combat/end',{},token);
        await request(url+'/spawn_character',{character_id:actor.id,tile_x:57,tile_y:10,assign_to_user_id:player.user_id,force_relocate:true},token);
        const located=await request(url+'/combat',null,token,'GET'),id=located.characters.find(character=>character.character_id===actor.id).location_character_id;
        const started=await request(url+'/combat/start',{location_character_ids:[id],initiator_location_character_id:id,initiator_first:true},token);
        if(started.current_character.facing_x!==-1||started.current_character.facing_y!==0)await request(url+'/combat/action',{location_character_id:id,action_key:'change_facing',facing_x:-1,facing_y:0,payment:'action'},player.access_token);
        for(const client of [page,peer])await client.until('L.characterPose('+actor.id+').x===57&&L.characterPose('+actor.id+').y===10&&L.getCombatState()?.current_character?.facing_x===-1','исходная точка и взгляд на перегородку');
        return id;
    };
    const preview=async()=> (await request(url+'/movement-preview',{character_id:actor.id,special_action:'climb',object_id:wall.id},player.access_token)).route;
    const complete=async()=>{
        await peer.evaluate('L.setLocationViewLevel(0);locationControls.target.set(57,2,10);locationControls.object.position.set(61,13,19);locationControls.update();void L.beginObjectClimbForTest('+wall.id+','+actor.id+')');
        await peer.until('document.querySelector("#traversal-payment-modal [data-traversal-payment]")','явная оплата перелезания перегородки');
        await click(peer,'[data-traversal-payment="action"]');
    };
    await setup();
    const direct=await preview();assert.deepEqual(direct.path.at(-1),[56,10]);
    await complete();await pose(56,10);
    fs.writeFileSync(path.join(safeSession.artifacts,'climb-partition-facing.png'),Buffer.from((await peer.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    const blockers=[];
    for(let index=0;index<3;index++)blockers.push(await request('/lobbies/'+lobby.id+'/characters',{name:'Блокирующий клетку '+index,data:{}},token));
    await setup();
    await request(url+'/spawn_character',{character_id:blockers[0].id,tile_x:56,tile_y:10},token);
    const adjacent=await preview();
    assert.ok(adjacent&&adjacent.path.at(-1)[0]===56&&[9,11].includes(adjacent.path.at(-1)[1]),'при занятом центре только соседняя боковая клетка');
    await complete();await pose(...adjacent.path.at(-1));
    await setup();
    for(const [index,y] of [[1,9],[2,11]])await request(url+'/spawn_character',{character_id:blockers[index].id,tile_x:56,tile_y:y},token);
    assert.equal(await preview(),null,'заняты все три: дальние клетки вдоль перегородки не используются');
    const before=(await request(url+'/combat',null,token,'GET')).current_character;
    const count=await peer.evaluate('climbRejected.length');
    await peer.evaluate('void socket.emit("move_in_location",{token:localStorage.getItem("access_token"),location_id:'+location.id+',character_id:'+actor.id+',x:56,y:10,special_action:"climb",object_id:'+wall.id+',climb_payment:"action"})');
    await peer.until('climbRejected.length>'+count,'сервер отклонил перелезание с тремя занятыми клетками');
    const after=(await request(url+'/combat',null,token,'GET')).current_character;
    assert.equal(after.action_points_current,before.action_points_current);assert.equal(after.movement_points_current,before.movement_points_current);
    await pose(57,10);
    await peer.call('Page.bringToFront');
    for(let attempt=0;attempt<3;attempt++){
        await peer.evaluate('(async()=>{locationControls.target.set(57.5,1,10.5);locationControls.object.position.set(62.5,16,20.5);locationControls.update();await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));locationControls.object.updateMatrixWorld(true)})()');
        const screen=await peer.evaluate('(()=>{const g=L.sceneGraphicsForTest(),model=g.characters.get('+actor.id+').model,p=model.position.clone();p.y+=1.3;p.project(g.camera);const r=g.renderer.domElement.getBoundingClientRect();return {x:r.left+(p.x+1)*r.width/2,y:r.top+(1-p.y)*r.height/2}})()');
        await peer.call('Input.dispatchMouseEvent',{type:'mouseMoved',...screen,buttons:0});
        for(const type of ['mousePressed','mouseReleased'])await peer.call('Input.dispatchMouseEvent',{type,...screen,button:'right',buttons:type==='mousePressed'?2:0,clickCount:1});
        if(await peer.evaluate('Boolean(document.querySelector(\'.combat-radial-action[aria-label="Уклонение"]\'))'))break;
    }
    await peer.until('document.querySelector(\'.combat-radial-action[aria-label="Уклонение"]\')','Уклонение в колесе по настоящему ПКМ');
    await click(peer,'.combat-radial-action[aria-label="Уклонение"]');
    await peer.until('document.querySelector("#trait-action-modal [type=submit]")','объявление отдельного Уклонения');
    await click(peer,'#trait-action-modal [type=submit]');
    for(const client of [page,peer])await client.until('L.getCombatState()?.characters?.find(c=>c.character_id==='+actor.id+')?.trait_state?.standaloneEvasion>=5','Уклонение применено через колесо у двух клиентов');
    await request(url+'/combat/end',{},token);
    await page.until('(()=>{const b=document.querySelector(".combat-hud-header .combat-start-btn");return b&&b.getClientRects().length})()','Начать бой видно на свёрнутой панели ГМа');
    const fresh=await request(sheetUrl,null,token,'GET');
    const raw='При общении с бандитами Харизма -20,\nесли они видят ваше лицо',clean='При общении с бандитами Харизма -20, если они видят ваше лицо';
    await request(sheetUrl,{data:{...fresh.data,basic:{...fresh.data.basic,background:{name:'Проверка описания',pluses:raw,minuses:raw}},
        features:{...fresh.data.features,specialTraits:[{name:'Проверка текста',effect:raw}]}}},token,'PUT');
    await page.evaluate('window.openCharacterSheet('+actor.id+',"basic")');
    await page.until('document.querySelector(\'[name="basic.background.minuses"]\')','описание предыстории в листе');
    assert.equal(await page.evaluate('document.querySelector(\'[name="basic.background.minuses"]\').value'),clean);
    await page.evaluate('window.openCharacterSheet('+actor.id+',"features")');
    await page.until('document.querySelector(\'[name="features.specialTraits.0.effect"]\')','описание особой черты');
    assert.equal(await page.evaluate('document.querySelector(\'[name="features.specialTraits.0.effect"]\').value'),clean);
    await page.evaluate('window.closeCharacterSheet()');
    fs.writeFileSync(path.join(safeSession.artifacts,'climb-partition-facing.json'),JSON.stringify({scene:'копия Припяти',wall:{id:wall.id,x:57,y:10},direct:direct.path.at(-1),adjacent:adjacent.path.at(-1),allThreeBlocked:true,noPaymentOnRejection:true,twoClients:true,ruleTextWrapRemoved:true},null,2));
};
