// Замки на копии Припяти: ГМ и игрок, реальный редактор и атомарные попытки.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {loadFixture,waitForDocument}=require('./durable_reload.browser.cjs');
module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'locks');assert.ok(safeSession.verified);
    const api=`/lobbies/${lobby.id}`,{fixture}=loadFixture();
    const location=await request(api+'/locations',{...fixture,objects:undefined,world_tile_x:7,world_tile_z:8},token),loc=api+'/locations/'+location.id;
    await request(loc+'/objects/batch',{objects:fixture.objects},token);
    const offered=await request(api+'/templates',null,token,'GET'),pick=offered.global.find(t=>t.name==='Отмычка');assert.ok(pick);
    await request('/auth/register',{username:'lock-player',email:'lock-player@example.test',password:'synthetic-lock-secret'},null);
    const login=await request('/auth/login',{username:'lock-player',password:'synthetic-lock-secret'},null),playerToken=login.access_token;
    await request(api+'/join',{},playerToken);
    const owner=await request(api+'/characters',{name:'Испытатель замков',data:{
        skills:{physical:{agility:{base:10},strength:{base:10}},other:{engineering:{base:10}}},
        inventory:{pockets:[{id:'physical-picks',templateId:pick.id,name:pick.name,category:pick.category,
            quantity:2,attributes:pick.attributes,weight:pick.weight,volume:pick.volume}],backpack:[]}}},playerToken);
    const placed=await request(loc+'/spawn_character',{character_id:owner.id,tile_x:43,tile_y:48},token);
    const chest=await request(loc+'/objects',{name:'Запертый ящик',type:'chest',tile_x:44,tile_y:48,
        properties:{locked:true,lock_difficulty:8,contents:[{id:'sealed-supply',name:'Секретный запас №232',category:'misc',quantity:1,weight:1,volume:1}]}},token);
    const door=await request(loc+'/objects',{name:'Запертая дверь',type:'door',tile_x:43,tile_y:49,
        properties:{locked:true,lock_difficulty:5,is_open:false}},token);
    const objectUrl=id=>api+'/locations/objects/'+id;
    const enter=async(client,auth,user)=>{
        const href=base+api+'/page?location='+location.id,epoch=await client.evaluate('performance.timeOrigin');
        await client.evaluate(`localStorage.setItem('access_token',${JSON.stringify(auth)});localStorage.setItem('user_id',${JSON.stringify(String(user))});true`);
        await client.call('Page.navigate',{url:href});await waitForDocument(client,{href,afterEpoch:epoch,label:'Замки в Припяти'});
        await client.until(`window.currentLocationId===${location.id}&&window.locationControls&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Припять готова');
        await client.evaluate(`(async()=>{window.L=await import('/static/js/locationScene.js');window.T=await import('three');window.UI=await import('/static/js/ui_interactions.js');
            window.mapAudit=(await import('/static/js/mapSceneContext.js')).getMapSceneContext();window.lockEvents=[];window.lockReads=[];
            for(const name of ['location_object_updated','location_objects_changed','location_editor_objects_changed'])socket.on(name,event=>lockEvents.push({name,...event}));
            const oldFetch=window.fetch;window.fetch=async(...args)=>{const result=await oldFetch(...args);if(String(args[0]).includes('/locations/objects/')&&(!args[1]?.method||args[1].method==='GET'))lockReads.push(String(args[0]));return result;};
            locationControls.enableDamping=false;locationControls.target.set(44.5,1,48.5);locationControls.object.position.set(47,16,63);locationControls.update();return true})()`);
    };
    let protocol,context,peer;
    try{
        await enter(page,token,lobby.gm_id);
        const version=await(await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();protocol=await connect(version.webSocketDebuggerUrl);
        context=await protocol.call('Target.createBrowserContext');const target=await protocol.call('Target.createTarget',{url:base,browserContextId:context.browserContextId});
        const tabs=await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json();peer=await connect(tabs.find(t=>t.id===target.targetId).webSocketDebuggerUrl);
        await peer.call('Page.enable');await peer.call('Runtime.enable');await waitForDocument(peer,{href:base,label:'Клиент владельца отмычек'});
        await enter(peer,playerToken,login.user_id);
        const publicChest=await request(objectUrl(chest.id),null,playerToken,'GET');assert.deepEqual(publicChest.properties.contents,[]);
        assert.equal((await request(objectUrl(chest.id),null,token,'GET')).properties.contents[0].id,'sealed-supply');
        const select=async object=>{
            await page.call('Page.bringToFront');await page.evaluate("L.setLocationEditMode(true);selectLocationEditorTool('select');UI.collapseMapToolsPanel();true");
            const point=await page.evaluate(`(()=>{const root=mapAudit.scene.children.find(mesh=>mesh.userData.locationObjectId===${object.id});
                const v=new T.Box3().setFromObject(root).getCenter(new T.Vector3()).project(mapAudit.camera),r=mapAudit.canvas.getBoundingClientRect();
                return{x:r.left+(v.x+1)*r.width/2,y:r.top+(1-v.y)*r.height/2}})()`);
            for(const type of ['mouseMoved','mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{type,...point,button:type==='mouseMoved'?'none':'left',buttons:type==='mousePressed'?1:0,clickCount:1});
            await page.until(`L.getSelectedLocationEditorAsset()?.selectionKey==='structure:${object.id}'`,'Выбран настоящий объект');
            await page.evaluate("UI.expandMapToolsPanel();document.querySelector('[data-selected-lock-fields]').open=true;true");
            await page.until("!document.querySelector('[data-selected-lock-fields]').hidden",'Настройка замка на вкладке Выбор');
        };
        const setDifficulty=async difficulty=>{
            await page.evaluate(`document.querySelector('[data-lock-setting=difficulty]').value='${difficulty}';document.querySelector('[data-lock-save]').click();true`);
            await page.until(`L.getSelectedLocationEditorAsset()?.lock?.difficulty===${difficulty}&&!L.getLocationEditorHistoryState().busy`,'Замок сохранён редактором');
        };
        await select(chest);await setDifficulty(10);await setDifficulty(8);
        assert.equal((await request(objectUrl(chest.id),null,token,'GET')).properties.contents[0].id,'sealed-supply');
        await page.evaluate('L.setLocationEditMode(false);UI.collapseMapToolsPanel();true');
        const start=()=>request(loc+'/combat/start',{location_character_ids:[placed.location_character_id],
            initiator_location_character_id:placed.location_character_id,initiator_first:true},token);
        const open=async(object,mode='tools')=>{
            const fresh=await request(objectUrl(object.id),null,playerToken,'GET');
            await peer.call('Page.bringToFront');
            await peer.evaluate(`(async()=>{const {showObjectLock}=await import('/static/js/objectLocks.js');await showObjectLock({lobbyId:${lobby.id},characterId:${owner.id},object:${JSON.stringify(fresh)},inCombat:true,onObject:L.updateLocationObject});return true})()`);
            await peer.until("document.querySelector('#object-lock-modal [data-lock-attempt]')",'Окно вскрытия');
            await peer.evaluate(`document.querySelector('[data-lock-mode]').value=${JSON.stringify(mode)};document.querySelector('[data-lock-mode]').dispatchEvent(new Event('change'));if(${JSON.stringify(mode)}==='tools')document.querySelector('[data-lock-tool]').value='0';true`);
        };
        const execute=async text=>{
            await peer.evaluate("document.querySelector('[data-lock-attempt]').click();true");
            await peer.until(`document.querySelector('[data-lock-result]')?.textContent.includes(${JSON.stringify(text)})`,'Исход попытки: '+text);
        };
        await start();await open(chest);const before=await request(loc+'/combat',null,playerToken,'GET');await execute('Отмычка сломалась');
        const failed=await request('/lobbies/characters/'+owner.id,null,playerToken,'GET');assert.equal(failed.data.inventory.pockets[0].quantity,1);
        assert.equal((await request(loc+'/combat',null,playerToken,'GET')).current_character.action_points_current,before.current_character.action_points_current-3);
        for(const [width,height]of [[1920,1080],[1600,900],[1366,768],[1280,720]]){
            await peer.call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
            await peer.until("(()=>{const r=document.querySelector('#object-lock-modal .modal-content').getBoundingClientRect();return r.x>=0&&r.y>=0&&r.bottom<=innerHeight&&r.right<=innerWidth})()",'Весь диалог на экране '+width);
            if(width===1280)fs.writeFileSync(path.join(safeSession.artifacts,'object-lock-failed-1280x720.png'),Buffer.from((await peer.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
        }
        await peer.evaluate("document.querySelector('#object-lock-modal .close').click();true");await request(loc+'/combat/end',{},token);
        await page.until('lockReads.length>=3','ГМ получил закрытое содержимое отдельным разрешённым запросом');
        await select(chest);await setDifficulty(10);await setDifficulty(8);await page.evaluate('L.setLocationEditMode(false);UI.collapseMapToolsPanel();true');
        await start();await open(chest);await execute('Замок открыт.');
        const unlocked=await request(objectUrl(chest.id),null,playerToken,'GET');assert.equal(unlocked.properties.contents[0].id,'sealed-supply');
        assert.ok(!unlocked.properties.locked);
        const packets=await peer.evaluate('lockEvents');
        const closed=packets.filter(event=>event.object?.properties?.locked||event.objects?.some(o=>o.properties?.locked));
        assert.ok(closed.length>=3);assert.ok(!JSON.stringify(closed).includes('Секретный запас'));
        await peer.evaluate("document.querySelector('#object-lock-modal .close').click();true");await request(loc+'/combat/end',{},token);
        await start();await open(door,'force');await execute('Замок заклинило');
        assert.ok((await request(objectUrl(door.id),null,playerToken,'GET')).properties.lock_jammed);
        await peer.evaluate("document.querySelector('#object-lock-modal .close').click();true");await request(loc+'/combat/end',{},token);
        await request(objectUrl(door.id),{properties:{lock_jammed:false}},token,'PATCH');
        await start();await open(door,'force');await execute('Замок открыт.');
        await request(objectUrl(door.id),{actor_character_id:owner.id,properties:{is_open:true}},playerToken,'PATCH');
        assert.ok((await request(objectUrl(door.id),null,playerToken,'GET')).properties.is_open);
        await request(loc+'/combat/end',{},token);
        console.log('Замки: два клиента копии Припяти/GPU; реальная настройка ГМа в Выборе, секретное содержимое скрыто в HTTP/сети, отмычка ломается из стопки, 3 ОД за попытку, успешный взлом, силовое заклинивание/сброс/открытие двери, четыре размера диалога.');
    }finally{peer?.close();if(context)await protocol.call('Target.disposeBrowserContext',{browserContextId:context.browserContextId});protocol?.close();}
};
