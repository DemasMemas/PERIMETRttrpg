// №54: профили каталога сохранены; большой запас ОД изолирует проверку лимита выстрелов.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {loadFixture,waitForDocument}=require('./durable_reload.browser.cjs');
module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'fire-rate');assert.ok(safeSession.verified);
    const source=JSON.parse(fs.readFileSync(path.join(__dirname,'../.private-testing/reports/catalog-sweep-2026-11-04/source.json'),'utf8'));
    const normalize=v=>String(v).toLowerCase().replace(/[×х*]/g,'x').replace(/,/g,'.').replace(/\s/g,'');
    const names=['Пистолет Зосимова','Дробовик Роза 33','ПП Пихта','Ручной пулемет Марьянова-39'];
    const weapons=[],reserves=[];
    for(const [index,name]of names.entries()){
        const raw=source.equipment.find(t=>t.name===name);assert.ok(raw);
        const template=await request(`/lobbies/${lobby.id}/templates`,raw,token);
        const ammo=source.equipment.find(t=>t.category==='ammo'&&normalize(t.attributes.caliber).startsWith(normalize(raw.attributes.caliber))&&!/газ|свет|см|сз/.test(t.name.toLowerCase()));assert.ok(ammo);
        const magazine=raw.attributes.fixedMagazine?null:source.equipment.find(t=>t.category==='magazine'
            &&normalize(t.attributes.caliber)===normalize(raw.attributes.caliber)&&t.attributes.capacity>=raw.attributes.fire_rate
            &&(index===0?t.name.startsWith('Магазин Пистолетный'):index===2?t.name.startsWith('Магазин ПП '):true));
        const capacity=raw.attributes.fixedMagazine?raw.attributes.magazine_size:magazine.attributes.capacity;
        const stack={id:`round-${index}`,name:ammo.name,category:'ammo',quantity:capacity,attributes:ammo.attributes};
        const weapon={id:`rate-gun-${index}`,templateId:template.id+1000000,name,model:name,category:'weapon',subcategory:raw.subcategory,
            attributes:raw.attributes,ammo:capacity,durability:100,maxDurability:100};
        if(raw.attributes.fixedMagazine)weapon.fixedAmmo=[stack];
        else weapon.installedMagazine={id:`rate-magazine-${index}`,name:magazine.name,category:'magazine',attributes:magazine.attributes,
            capacity,ammo:[stack],currentAmmo:capacity};
        weapons.push(weapon);reserves.push({...stack,id:`reserve-${index}`,quantity:100});
    }
    const api=`/lobbies/${lobby.id}`,{fixture}=loadFixture();
    const location=await request(api+'/locations',{...fixture,objects:undefined,world_tile_x:7,world_tile_z:8},token);
    const loc=api+'/locations/'+location.id,combat=loc+'/combat';
    await request(loc+'/objects/batch',{objects:fixture.objects},token);
    const target=await request(loc+'/objects',{type:'wall',name:'Мишень скорострельности',tile_x:41,tile_y:50,
        properties:{dimensions:{width:1,depth:1,height:2},cover_max_hp:100000,cover_hp:100000}},token);
    const actor=await request(api+'/characters',{name:'Проверка скорострельности',data:{weapons,
        combat:{action_points:300},skills:{physical:{strength:{base:20},shooting:{base:20},accuracy:{base:20}}},
        health:{current:700,max:700,effects:[]},inventory:{pockets:reserves,backpack:[]}}},token);
    await request(loc+'/spawn_character',{character_id:actor.id,tile_x:41,tile_y:48},token);
    await request('/auth/register',{username:'rate-observer',email:'rate-observer@example.test',password:'synthetic-rate-password'});
    const observer=await request('/auth/login',{username:'rate-observer',password:'synthetic-rate-password'});
    await request(api+'/join',{},observer.access_token);
    await request('/lobbies/characters/'+actor.id+'/visibility',{visible_to:[observer.user_id],editable_to:[]},token,'PUT');
    const version=await(await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();let protocol,context,peer;
    try{
        protocol=await connect(version.webSocketDebuggerUrl);context=await protocol.call('Target.createBrowserContext');
        const peerTarget=await protocol.call('Target.createTarget',{url:base,browserContextId:context.browserContextId});
        const info=(await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json()).find(t=>t.id===peerTarget.targetId);
        peer=await connect(info.webSocketDebuggerUrl);await peer.call('Page.enable');await peer.call('Runtime.enable');
        await waitForDocument(peer,{href:base,label:'Наблюдатель лимита выстрелов'});
        await peer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(observer.access_token)});localStorage.setItem('user_id',${JSON.stringify(String(observer.user_id))});true`);
        for(const client of [page,peer]){
            const href=base+api+'/page?location='+location.id,epoch=await client.evaluate('performance.timeOrigin');
            await client.call('Page.navigate',{url:href});await waitForDocument(client,{href,afterEpoch:epoch,label:'Припять: скорострельность'});
            await client.until(`window.socket?.connected&&window.currentLocationId===${location.id}&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Сцена готова');
            await client.evaluate(`window.openCharacterSheet(${actor.id},'equipment');window.__rateUpdates=[];socket.on('character_data_updated',e=>window.__rateUpdates.push(e));true`);
            await client.until('document.querySelector("#weapons-container")?.textContent.includes("Пистолет Зосимова")','Оружие в листе');
        }
        let state=await request(combat,null,token,'GET');const actorId=state.characters.find(c=>c.character_id===actor.id).location_character_id;
        await request(combat+'/start',{location_character_ids:[actorId],initiator_location_character_id:actorId,initiator_first:true},token);
        const action=payload=>request(combat+'/action',{location_character_id:actorId,...payload},token);
        const read=()=>request('/lobbies/characters/'+actor.id,null,token,'GET');
        const count=w=>w.installedMagazine?(w.installedMagazine.ammo||[]).reduce((s,v)=>s+v.quantity,0):(w.fixedAmmo||[]).reduce((s,v)=>s+v.quantity,0);
        const rows=[];
        for(let index=0;index<weapons.length;index++){
            await action({action_key:'draw_weapon',weapon_index:index});
            const rate=weapons[index].attributes.fire_rate,before=await read();
            const fire=shots=>action({action_key:'attack',weapon_index:index,fire_mode:'unaimed',shot_count:shots,
                action_points:index===0?1:2,target_object_id:target.id});
            for(let shot=0;shot<rate;shot++)await fire(1);
            let after=await read();assert.equal(count(after.data.weapons[index]),count(before.data.weapons[index])-rate);
            if(index===1){
                const sourceIndex=after.data.inventory.pockets.findIndex(item=>normalize(item.attributes.caliber).startsWith(normalize(weapons[index].attributes.caliber)));
                assert.ok(sourceIndex>=0);
                await action({action_key:'load_ammunition',loading:{weapon_index:index,source_path:['inventory','pockets',sourceIndex],
                    quantity:2,action_points:20,free_actions:0}}); // Переплата намеренна: цена зарядки проверяется отдельно.
                after=await read();assert.equal(count(after.data.weapons[index]),2);
            }
            const stateBefore=await request(combat,null,token,'GET'),ammoBefore=count(after.data.weapons[index]);
            const denied=await fetch(base+combat+'/action',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},
                body:JSON.stringify({location_character_id:actorId,action_key:'attack',weapon_index:index,fire_mode:'unaimed',
                    shot_count:1,action_points:index===0?1:2,target_object_id:target.id})});
            const error=await denied.json();assert.equal(denied.status,400);assert.match(error.error.message,/скорострельность/);
            after=await read();state=await request(combat,null,token,'GET');
            assert.equal(count(after.data.weapons[index]),ammoBefore);assert.equal(state.current_character.action_points_current,stateBefore.current_character.action_points_current);
            for(const client of [page,peer])await client.until(`window.__rateUpdates.some(e=>e.character_id===${actor.id}&&e.updates?.data?.health?.combatMeta?.weaponShots?.shots?.['${index}']===${rate})`,'Оба клиента получили полный счётчик');
            rows.push({weapon:names[index],catalogRate:rate,shots:rate,deniedWithoutAmmoOrAP:true,reloadedSameTurn:index===1});
            await request(combat+'/end_turn',{location_character_id:actorId},token);
            await fire(1); // Следующий ход действительно открывает новый лимит.
            await request(combat+'/end_turn',{location_character_id:actorId},token);
        }
        const result={status:'PASS',runId:safeSession.runId,scene:'Копия Припяти',controlledAttackRoll:15,
            actionPointFixture:300,targetCoverHP:100000,twoClients:true,rows};
        fs.writeFileSync(path.join(safeSession.artifacts,'fire-rate.json'),JSON.stringify(result,null,2));
    }finally{await peer?.close();if(context)await protocol?.call('Target.disposeBrowserContext',{browserContextId:context.browserContextId});await protocol?.close();}
};
