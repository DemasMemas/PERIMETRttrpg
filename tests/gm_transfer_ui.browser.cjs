const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
module.exports=async({page,peer,request,token,lobby,safeSession,location,actor,player})=>{
    const api=`/lobbies/${lobby.id}`,url=api+`/locations/${location.id}`;
    const sheet=await request(`/lobbies/characters/${actor.id}`,null,token,'GET');
    await request(`/lobbies/characters/${actor.id}`,{data:{...sheet.data,health:{...sheet.data.health,current:700,painLevel:10}}},token,'PUT');
    await request(url+'/spawn_character',{character_id:actor.id,tile_x:41,tile_y:48,floor_level:0,assign_to_user_id:player.user_id},token);
    const other=await request(api+'/characters',{name:'Ход другого персонажа',data:{}},token);
    const otherPlaced=await request(url+'/spawn_character',{character_id:other.id,tile_x:43,tile_y:48},token);
    await request(url+'/combat/start',{location_character_ids:[otherPlaced.location_character_id],initiator_location_character_id:otherPlaced.location_character_id,initiator_first:true},token);
    const before=await request(url+'/combat',null,token,'GET');
    const beforeActor=before.characters.find(c=>c.character_id===actor.id);
    assert.equal(before.current_character.character_id,other.id);
    assert.equal(beforeActor.condition.state,'pain_shock');
    const result={scene:'Припять',before:beforeActor,checks:[]};
    const save=()=>fs.writeFileSync(path.join(safeSession.artifacts,'gm-transfer-ui.json'),JSON.stringify(result,null,2));
    const click=async(point,button='left')=>{
        await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',...point});
        await page.call('Input.dispatchMouseEvent',{type:'mousePressed',button,buttons:button==='right'?2:1,clickCount:1,...point});
        await page.call('Input.dispatchMouseEvent',{type:'mouseReleased',button,buttons:0,clickCount:1,...point});
    };
    const openTransfer=async()=>{
        await page.call('Page.bringToFront');
        await page.evaluate(`L.setLocationEditMode(false);L.setLocationViewLevel(L.characterSpatialPose(${actor.id}).level);(()=>{const p=L.characterSpatialPose(${actor.id});locationControls.object.position.set(p.x,p.height+10,p.z+9);locationControls.target.set(p.x,p.height+1,p.z);locationControls.update();locationControls.object.updateMatrixWorld(true)})()`);
        const point=await page.evaluate(`(()=>{const v=new T.Vector3(...L.characterModelCenterForTest(${actor.id})).project(locationControls.object),r=document.querySelector('#location-container canvas').getBoundingClientRect();return{x:r.left+(v.x+1)*r.width/2,y:r.top+(1-v.y)*r.height/2}})()`);
        await click(point,'right');
        await page.until(`(()=>{const b=document.querySelector('[aria-label="Перенос ГМа"]');return b&&!b.disabled&&b.getClientRects().length})()`,'доступный перенос в чужой ход и при болевом шоке');
        const button=await page.evaluate(`(()=>{const r=document.querySelector('[aria-label="Перенос ГМа"]').getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
        await click(button);
        await page.until('L.gmRelocationStateForTest().pending','режим переноса ГМа');
    };
    try {
        for(const c of [page,peer])await c.until(`L.characterPose(${actor.id}).x===41&&L.characterPose(${actor.id}).y===48`,'исходная позиция');
        await openTransfer();
        await page.call('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
        await page.call('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27});
        await page.until('!L.gmRelocationStateForTest().pending&&!L.gmRelocationStateForTest().ghost','отмена Escape');
        assert.equal((await request(url+'/combat',null,token,'GET')).characters.find(c=>c.character_id===actor.id).x,41);
        result.checks.push('Escape cancels');save();
        await openTransfer();await click({x:350,y:300},'right');
        await page.until('!L.gmRelocationStateForTest().pending&&!L.gmRelocationStateForTest().ghost','отмена ПКМ');
        assert.equal((await request(url+'/combat',null,token,'GET')).characters.find(c=>c.character_id===actor.id).x,41);
        result.checks.push('right click cancels');save();
        for(const target of [{x:45.3,y:48.7,level:2,height:5.8},{x:5.5,y:5,level:0,height:1}]) {
            await openTransfer();
            await page.evaluate(`L.setLocationViewLevel(${target.level});locationControls.target.set(${target.x+.5},${target.height},${target.y+.5});locationControls.object.position.set(${target.x+.5},${target.height+12},${target.y+10});locationControls.update();locationControls.object.updateMatrixWorld(true)`);
            const point=await page.evaluate(`(()=>{const v=new T.Vector3(${target.x+.5},${target.height},${target.y+.5}).project(locationControls.object),r=document.querySelector('#location-container canvas').getBoundingClientRect();return{x:r.left+(v.x+1)*r.width/2,y:r.top+(1-v.y)*r.height/2}})()`);
            await page.call('Input.dispatchMouseEvent',{type:'mouseMoved',...point});
            await page.until('L.gmRelocationStateForTest().ghost','призрак назначения');
            if(target.level===2)fs.writeFileSync(path.join(safeSession.artifacts,'gm-transfer-preview.png'),Buffer.from((await page.evaluate('L.captureCurrentSceneForTest()')).split(',')[1],'base64'));
            await click(point);
            for(const c of [page,peer])await c.until(`(()=>{const p=L.characterPose(${actor.id}),s=L.characterSpatialPose(${actor.id});return Math.abs(p.x-${target.x})<1e-8&&Math.abs(p.y-${target.y})<1e-8&&s.level===${target.level}&&!s.animating})()`,'принудительный перенос у двух клиентов');
            const state=await request(url+'/combat',null,token,'GET'),after=state.characters.find(c=>c.character_id===actor.id);
            assert.equal(state.current_character.character_id,other.id);
            for(const key of ['action_points_current','free_actions_current','movement_points_current','controlled_by','facing_x','facing_y','posture'])assert.equal(after[key],beforeActor[key],key);
            assert.equal(after.condition.state,'pain_shock');
            assert.equal(await page.evaluate('L.gmRelocationStateForTest().ghost'),false);
            result.checks.push({target,after});save();
        }
        await page.evaluate(`L.openOwnerSelectionModal(${actor.id},42.1,48.2,${player.user_id})`);
        assert.equal(await page.evaluate('Boolean(document.querySelector("#owner-select"))'),false);
        for(const c of [page,peer])await c.until(`L.characterPose(${actor.id}).x===42.1&&L.characterPose(${actor.id}).y===48.2`,'перенос уже размещённого без выбора владельца');
        assert.equal((await request(url+'/combat',null,token,'GET')).characters.find(c=>c.character_id===actor.id).controlled_by,player.user_id);
        result.checks.push('existing placement keeps controller without modal');save();
        // Проверка запрета игроку проходит через уже доступный HTTP-клиент.
        const playerAttempt=await peer.evaluate(`(async()=>{const r=await fetch(${JSON.stringify(url+'/spawn_character')},{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+localStorage.getItem('access_token')},body:JSON.stringify({character_id:${actor.id},tile_x:1,tile_y:1,force_relocate:true})});await r.text();return r.status})()`);
        assert.equal(playerAttempt,403);result.checks.push('player denied');
        await peer.evaluate('window.__gmTransferBeforeReload=true');
        await peer.call('Page.reload');
        await peer.until('!window.__gmTransferBeforeReload&&typeof enterLocation==="function"&&window.socket?.connected','перезагрузка игрока');
        await peer.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');await enterLocation(${location.id})})()`);
        await peer.until(`(()=>{try{return L.characterPose(${actor.id}).x===42.1&&L.characterPose(${actor.id}).y===48.2&&L.characterSpatialPose(${actor.id}).level===0}catch{return false}})()`,'перенос сохранён после reload');
        result.reloaded=true;result.status='READY_FOR_REVIEW';
        fs.writeFileSync(path.join(safeSession.artifacts,'gm-transfer-ui.json'),JSON.stringify(result,null,2));
        console.log('PASS: Перенос ГМа через настоящее меню, Esc/ПКМ, чужой ход, болевой шок, любой этаж/стена, два клиента и reload');
    } finally {
        await request(url+'/combat/end',{},token);
    }
};
