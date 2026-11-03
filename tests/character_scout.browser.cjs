const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');

module.exports=async({page,peer,request,token,lobby,location,url,safeSession,click})=>{
    await page.evaluate('window.closeCharacterSheet?.()');
    await request('/auth/register',{username:'scout-player',email:'scout-player@example.test',password:'scout-test-password'});
    const login=await request('/auth/login',{username:'scout-player',password:'scout-test-password'});
    const playerToken=login.access_token,playerId=Number(JSON.parse(Buffer.from(playerToken.split('.')[1],'base64url')).sub);
    await request('/lobbies/'+lobby.id+'/join',{},playerToken);
    const observer=await request('/lobbies/'+lobby.id+'/characters',{name:'Наблюдатель до размещения',data:{
        basic:{background:{name:'Наблюдатель'}},health:{current:700,max:700,effects:[]},
        skills:{physical:{awareness:{base:10},strength:{base:10}},other:{tactics:{base:10}}},
        weapons:[{name:'Оптика наблюдения',range:200}],inventory:{pockets:[]},equipment:{}}},playerToken);
    const rear=await request('/lobbies/'+lobby.id+'/characters',{name:'Цель за наблюдателем',data:{}},token);
    await request(url+'/spawn_character',{character_id:rear.id,tile_x:41,tile_y:46},token);
    const before=await request(url+'/combat',null,token,'GET');
    await page.evaluate('window.openCharacterSheet('+observer.id+',"basic")');
    await page.until('document.querySelector(".scout-territory-btn")?.getClientRects().length','ГМ открыл лист неразмещённого наблюдателя');
    await click('.scout-territory-btn');
    await page.until('document.querySelector(".scout-configure")?.getClientRects().length','ГМ может задать точку осмотра');
    await page.evaluate('(()=>{const g=L.sceneGraphicsForTest();g.controls.target.set(41.5,1,48.5);g.camera.position.set(46.5,20,58.5);g.controls.update()})()');
    await click('.scout-configure');
    for(const [x,z] of [[41.5,48.5],[41.5,50.5]]){
        const position=await page.evaluate('(()=>{const g=L.sceneGraphicsForTest(),p=g.camera.position.clone().set('+x+',1,'+z+').project(g.camera),r=g.renderer.domElement.getBoundingClientRect();return {x:r.left+(p.x+1)*r.width/2,y:r.top+(1-p.y)*r.height/2}})()');
        for(const type of ['mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{type,...position,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1});
    }
    await page.until('document.querySelector(".scout-point")?.textContent.includes("Точка ГМа")','точка и направление сохранены настоящими щелчками');
    const point=await request('/lobbies/characters/'+observer.id+'/location-scout',null,playerToken,'GET');
    assert.ok(Math.abs(point.point.x-41)<.1&&Math.abs(point.point.y-48)<.1&&point.point.facing_y>.99);
    await click('#trait-action-modal .trait-close');await page.evaluate('window.closeCharacterSheet()');
    await peer.evaluate('localStorage.setItem("access_token",'+JSON.stringify(playerToken)+');localStorage.setItem("user_id",'+JSON.stringify(String(playerId))+')');
    const lobbyPage=await page.evaluate('location.origin+"/lobbies/"+window.currentLobbyId+"/page"');
    await peer.call('Page.navigate',{url:lobbyPage});
    await peer.until('Boolean(window.openCharacterSheet)&&window.isGM===false','второй клиент вошёл настоящим игроком');
    let view;
    for(let attempt=0;attempt<5;attempt++){
        await peer.evaluate('window.openCharacterSheet('+observer.id+',"basic")');
        await peer.until('document.querySelector(".scout-territory-btn")?.getClientRects().length','осмотр доступен игроку до размещения');
        await click('.scout-territory-btn',peer);
        await peer.until('document.querySelector("#trait-action-modal [name=instrument]")','инструмент осмотра');
        await peer.evaluate('document.querySelector("#trait-action-modal [name=instrument]").value="weapon:0"');
        await click('#trait-action-modal [type=submit]',peer);
        await peer.until('window.locationScoutView&&window.isLocationActive&&document.getElementById("scene-loading-screen").classList.contains("is-hidden")','просмотр через точку ГМа');
        view=await peer.evaluate('window.locationScoutView');
        if(view.check.success)break;
        await peer.evaluate('window.exitLocation()');
    }
    assert.ok(view.check.success,'осмотр получил успешный настоящий бросок');
    await peer.evaluate('(async()=>{window.L=await import("/static/js/locationScene.js")})()');
    await peer.until('L.getCombatState()?.characters?.length>0','состояние подлокации получено по сети');
    const front=before.characters.find(person=>Math.abs(person.x-41)<.5&&person.y>49&&person.y<51&&!person.gm_hidden&&!person.camouflaged);
    assert.ok(front,'на улице впереди есть настоящая цель');
    assert.equal(await peer.evaluate('L.characterVisible('+front.character_id+')'),true,'цель внутри открытого конуса видна');
    assert.equal(await peer.evaluate('L.characterVisible('+rear.id+')'),false,'цель позади наблюдателя скрыта');
    assert.ok(await peer.evaluate('Boolean(document.querySelector(".scout-view-exit"))'),'виден режим осмотра');
    fs.writeFileSync(path.join(safeSession.artifacts,'scout-before-placement.png'),Buffer.from((await peer.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    const after=await request(url+'/combat',null,token,'GET');
    assert.ok(!after.characters.some(person=>person.character_id===observer.id),'осмотр не разместил персонажа');
    for(const actor of before.characters){
        const actual=after.characters.find(person=>person.character_id===actor.character_id);
        assert.equal(actual.x,actor.x);assert.equal(actual.y,actor.y);assert.equal(actual.action_points_current,actor.action_points_current);
    }
    await click('.scout-view-exit',peer);
    await peer.until('!window.isLocationActive&&!window.locationScoutView','осмотр завершён без входа персонажа в мир');
    fs.writeFileSync(path.join(safeSession.artifacts,'scout-before-placement.json'),JSON.stringify({scene:'копия Припяти',gmTwoClicks:true,realPlayer:true,noPlacement:true,noMovementOrApSpent:true,coneHidesRearTarget:true,exitRestoresWorldMap:true},null,2));
};
