const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,peer,request,token,lobby,safeSession,location,player})=>{
    const api=`/lobbies/${lobby.id}`,url=api+`/locations/${location.id}`;
    const actor=await request(api+'/characters',{name:'Возврат Пси-зомби',data:{
        skills:{physical:{shooting:{base:13,bonus:0,xp:2}}},
        inventory:{backpack:[],pockets:[{id:'restore-loot',name:'Сохранённая вещь после Пси',quantity:2,weight:.1}]},weapons:[],
    }},player.access_token);
    const sheet=await request(`/lobbies/characters/${actor.id}`,null,token,'GET');
    await request(`/lobbies/characters/${actor.id}`,{data:{...sheet.data,health:{...sheet.data.health,current:1,psyState:50,painLevel:0,effects:[]}}},token,'PUT');
    const placed=await request(url+'/spawn_character',{character_id:actor.id,tile_x:44,tile_y:48,assign_to_user_id:player.user_id},token);
    await request(url+'/combat/start',{location_character_ids:[placed.location_character_id],initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
    try{
        await request(url+'/combat/end_turn',{location_character_id:placed.location_character_id},token);
        const zombie=await request(`/lobbies/characters/${actor.id}`,null,token,'GET');
        assert.equal(zombie.data.is_mutant,true,'реальная смерть от сброса Пси превращает в Зомбированного');
        assert.equal(zombie.data.basic.former_character_id,actor.id);
        await request(`/lobbies/characters/${actor.id}`,{data:{...zombie.data,health:{...zombie.data.health,current:33}}},token,'PUT');
        zombie.data=(await request(`/lobbies/characters/${actor.id}`,null,token,'GET')).data;
        for(const c of [page,peer])await c.until(`L.characterIsMutantForTest(${actor.id})`,'зомбирование у двух клиентов');
        const denied=await peer.evaluate(`(async()=>{const r=await fetch(${JSON.stringify(api+`/characters/${actor.id}/restore-player`)},{method:'POST',headers:{Authorization:'Bearer '+localStorage.getItem('access_token')}});await r.text();return r.status})()`);
        assert.equal(denied,403);
        await page.evaluate(`(async()=>{const chars=await import('/static/js/characters.js');await chars.openMutantCard(${actor.id})})()`);
        await page.until('document.querySelector(".mutant-card-restore-player")','возврат обычного листа в карточке зомби');
        await page.evaluate('document.querySelector(".mutant-card-restore-player").scrollIntoView({block:"center"})');
        await page.call('Page.bringToFront');
        const point=await page.evaluate('(()=>{const r=document.querySelector(".mutant-card-restore-player").getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()');
        await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',...point});
        await page.call('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',buttons:1,clickCount:1,...point});
        await page.call('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',buttons:0,clickCount:1,...point});
        await page.until('[...document.querySelectorAll("#sheet-tab-inventory input")].some(input=>input.value==="Сохранённая вещь после Пси")','обычный лист открыт на инвентаре');
        const restored=await request(`/lobbies/characters/${actor.id}`,null,token,'GET');
        assert.equal(Boolean(restored.data.is_mutant||restored.data.basic?.is_mutant),false);
        assert.deepEqual(restored.data.inventory,zombie.data.inventory);
        assert.equal(restored.data.skills.physical.shooting.base,13);
        assert.equal(restored.data.health.current,zombie.data.health.current,'возврат листа не лечит');
        for(const c of [page,peer])await c.until(`!L.characterIsMutantForTest(${actor.id})`,'обычная модель у двух клиентов');
        const state=await request(url+'/combat',null,token,'GET');
        assert.equal(state.characters.find(c=>c.character_id===actor.id).controlled_by,player.user_id);
        fs.writeFileSync(path.join(safeSession.artifacts,'zombie-restored-inventory.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        fs.writeFileSync(path.join(safeSession.artifacts,'zombie-restore.json'),JSON.stringify({status:'READY_FOR_REVIEW',realPsiDeath:true,playerDenied:denied,inventory:restored.data.inventory,shooting:restored.data.skills.physical.shooting,healthBefore:zombie.data.health.current,healthAfter:restored.data.health.current,twoClients:true},null,2));
        console.log('PASS: реальная смерть от Пси, карточка зомби, возврат обычного листа/инвентаря, вещи и навыки сохранены, два клиента');
    }finally{await request(url+'/combat/end',{},token);}
};
