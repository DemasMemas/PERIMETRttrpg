// A15–17: настоящий урон, два обычных игрока и отзыв приватной подписки.
// Импорт только определяет функции; запуск разрешён отдельным owned-file gate.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {createHash}=require('node:crypto');
const {assertGpu}=require('./helpers/browser_isolation.cjs');
const FIXTURE=path.resolve(__dirname,'../tmp/pripyat-regression.json');
const WINDOW_MS=1800,PASSWORD='synthetic-damage-password';
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function projectHealth(data){return{id:Number(data?._character_id),revision:Number(data?._revision),hp:Number(data?.health?.current),max:Number(data?.health?.max)};}
function zoneKey(value){
    const order=v=>Array.isArray(v)?v.map(order):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,order(v[k])])):v;
    return JSON.stringify(order(value));
}
function loadFixture(io=fs){
    const stat=io.lstatSync(FIXTURE);
    assert.ok(stat.isFile()&&!stat.isSymbolicLink(),'Нужен обычный сохранённый JSON Припяти');
    assert.equal(io.realpathSync(FIXTURE),FIXTURE,'Перенаправленный JSON не допускается');
    const bytes=io.readFileSync(FIXTURE),fixture=JSON.parse(bytes.toString('utf8'));
    assert.ok(fixture.name?.includes('Припять')&&fixture.grid_width===72&&fixture.grid_height===72);
    assert.ok(Array.isArray(fixture.objects)&&fixture.objects.length>0);
    return{fixture,sha256:createHash('sha256').update(bytes).digest('hex')};
}
function assertDamage(before,after,attack,targetId){
    assert.equal(before.id,targetId);assert.equal(after.id,targetId);
    assert.ok(Number.isInteger(before.revision)&&after.revision>before.revision,'Новая сохранённая ревизия');
    assert.ok(before.hp>after.hp&&after.hp>0,'Реальный несмертельный урон');
    const hits=(attack?.results||[]).filter(hit=>hit.target_character_id===targetId&&hit.hit);
    assert.ok(hits.length>0,'Успешный удар по выбранному персонажу');
    const damage=hits.reduce((sum,hit)=>sum+Number(hit.combined_damage??hit.damage??0),0);
    assert.ok(damage>0);assert.equal(Number(attack.damage_total),damage);
    assert.equal(before.hp-after.hp,damage,'Урон ответа равен изменению сохранённого пула ОЗ');
}
function assertLiveWindow(window){
    assert.ok(window.elapsed>=WINDOW_MS&&window.elapsed<10000,'Конечный интервал наблюдения');
    assert.ok(window.samples>=2&&window.connected&&window.sameSocket&&window.disconnects===0);
    assert.ok(['polling','websocket'].includes(window.serverTransport),'Транспорт подтверждён server network_ping');
}
function assertPrivateEvent(evidence,after,from){
    const event=evidence.private.find(e=>e.seq>from&&e.payload.character_id===after.id&&e.payload.source==='combat'
        &&e.snapshot?.revision>=after.revision&&e.snapshot.hp===after.hp);
    assert.ok(event,'Получено фактическое приватное обновление урона');return event;
}
function assertRevoked(owner,viewer,after,barriers,windows,controlKey){
    assertLiveWindow(windows.owner);assertLiveWindow(windows.viewer);
    const ownEvent=assertPrivateEvent(owner,after,barriers.owner);
    for(const [role,evidence]of [['owner',owner],['viewer',viewer]]){
        const control=evidence.public.find(e=>e.seq>barriers[role]&&e.characterId===after.id&&e.zonesKey===controlKey);
        assert.ok(control,'Получен соответствующий урону combat_character_updated после приватного события');
        if(role==='owner')assert.ok(control.seq>ownEvent.seq,'Порядок приватного и контрольного события подтверждён');
        assert.equal(evidence.disconnects,0);assert.ok(evidence.connected&&evidence.sameSocket);
    }
    assert.deepEqual(viewer.private.filter(e=>e.seq>barriers.viewer),[],'После отзыва нет нового character_data_updated');
}
function sheetSnapshot(){
    const visible=node=>Boolean(node&&node.getClientRects().length&&getComputedStyle(node).display!=='none'&&getComputedStyle(node).visibility!=='hidden');
    const fields=[...document.querySelectorAll('#sheet-content [name="health.current"]')].filter(visible);
    return{sheetVisible:visible(document.querySelector('#character-sheet-modal')),hp:fields.length===1?Number(fields[0].value):null,
        fieldCount:fields.length,disabled:fields.length===1?Boolean(fields[0].disabled||fields[0].readOnly):null,
        opens:window.damageEvidence?.opens,epoch:performance.timeOrigin};
}
function collector(characterId,project,canonical){
    const state=window.damageEvidence={seq:0,private:[],public:[],chat:[],access:[],opens:0,disconnects:0,initialSocket:socket.id};
    const transport=()=>socket.io?.engine?.transport?.name||null;
    document.addEventListener('character-sheet-opened',()=>state.opens++);
    socket.on('disconnect',()=>state.disconnects++);
    socket.on('character_data_updated',message=>{
        if(Number(message.character_id)!==characterId)return;
        // Сохраняется фактический wire-payload в узкой проекции, без токенов/произвольного листа.
        state.private.push({seq:++state.seq,event:'character_data_updated',transport:transport(),payload:{
            character_id:Number(message.character_id),source:message.source||null,
            updates:{data:message.updates?.data?{_character_id:message.updates.data._character_id,_revision:message.updates.data._revision,
                health:{current:message.updates.data.health?.current,max:message.updates.data.health?.max}}:null},
            updated_by:message.updated_by??null},snapshot:message.updates?.data?project(message.updates.data):null});
    });
    socket.on('combat_character_updated',message=>{if(Number(message.character_id)===characterId)
        state.public.push({seq:++state.seq,event:'combat_character_updated',characterId:Number(message.character_id),zonesKey:canonical(message.hp_zones),transport:transport()});});
    socket.on('character_updated',message=>{if(Number(message.id)===characterId)
        state.access.push({seq:++state.seq,visibleTo:message.visible_to,editableTo:message.editable_to});});
    socket.on('new_message',message=>state.chat.push({seq:++state.seq,event:'new_message',username:message.username,
        message:message.message,timestamp:message.timestamp,transport:transport()}));
    return true;
}
function readEvidence(){const e=window.damageEvidence;return{seq:e.seq,private:e.private,public:e.public,chat:e.chat,access:e.access,
    opens:e.opens,disconnects:e.disconnects,connected:socket.connected,sameSocket:socket.id===e.initialSocket};}
async function liveWindow(duration){
    const e=window.damageEvidence,start=performance.now();let samples=0;
    do{await new Promise(resolve=>setTimeout(resolve,100));samples++;
        if(!socket.connected||socket.id!==e.initialSocket||e.disconnects)throw Error('Соединение потеряно в отрицательном интервале');
    }while(performance.now()-start<duration);
    const ack=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('network_ping не подтверждён')),5000);
        socket.emit('network_ping',{},reply=>{clearTimeout(timer);resolve(reply);});});
    return{elapsed:performance.now()-start,samples,connected:socket.connected,sameSocket:socket.id===e.initialSocket,
        disconnects:e.disconnects,serverTransport:ack?.transport,serverTimeMs:ack?.server_time_ms};
}
const expression=fn=>`(${fn.toString()})()`;
const redact=error=>String(error?.message||error).replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g,'[тестовый токен]').replaceAll(PASSWORD,'[тестовый пароль]');

module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession})=>{
    assert.ok(safeSession?.verified&&process.env.VTT_SAFE_SCENARIO==='damage-revoke'&&process.env.VTT_SAFE_DB_MODE==='owned-file');
    const result={task:'BROWSER-DAMAGE-REVOKE',status:'RUNNING',runId:safeSession.runId,port:Number(new URL(base).port),phases:[]};
    const save=()=>fs.writeFileSync(path.join(safeSession.artifacts,'damage-revoke.json'),JSON.stringify(result,null,2));
    const record=(stage,value={})=>{result.phases.push({stage,...value});save();};save();
    let protocol,context,peer;
    const screenshot=async(client,label)=>fs.writeFileSync(path.join(safeSession.artifacts,`damage-${label}.png`),
        Buffer.from((await client.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    try{
        const {fixture,sha256}=loadFixture();
        const api=`/lobbies/${lobby.id}`;
        const location=await request(api+'/locations',{...fixture,objects:undefined,world_tile_x:8,world_tile_z:8},token);
        const locationUrl=api+'/locations/'+location.id;
        await request(locationUrl+'/objects/batch',{objects:fixture.objects},token);
        record('pripyat-mounted',{fixture:'tmp/pripyat-regression.json',sha256,name:fixture.name,locationId:location.id,objects:fixture.objects.length});
        const makePlayer=async name=>{
            await request('/auth/register',{username:name,email:name+'@example.test',password:PASSWORD});
            const login=await request('/auth/login',{username:name,password:PASSWORD});
            await request(api+'/join',{},login.access_token);return login;
        };
        const owner=await makePlayer('damage-owner'),viewer=await makePlayer('damage-viewer');
        assert.notEqual(owner.user_id,viewer.user_id);
        const victim=await request(api+'/characters',{name:'Получатель урона A15',data:{health:{current:700,max:700,effects:[]},
            skills:{physical:{strength:{base:10,bonus:0},will:{base:40,bonus:0}}},inventory:{pockets:[],backpack:[]},equipment:{}}},owner.access_token);
        const attacker=await request(api+'/characters',{name:'Атакующий A15',data:{health:{current:700,max:700,effects:[]},
            combat:{action_points:20},skills:{physical:{strength:{base:10,bonus:0},melee:{base:40,bonus:0}}}}},token);
        assert.equal(victim.owner_id,owner.user_id);assert.notEqual(victim.owner_id,lobby.gm_id);
        await request(locationUrl+'/spawn_character',{character_id:attacker.id,tile_x:41,tile_y:48},token);
        await request(locationUrl+'/spawn_character',{character_id:victim.id,tile_x:42,tile_y:48,assign_to_user_id:owner.user_id},token);
        const characterUrl=`/lobbies/characters/${victim.id}`;
        await request(characterUrl+'/visibility',{visible_to:[viewer.user_id],editable_to:[]},token,'PUT');
        const ownerState=await request(characterUrl,null,owner.access_token,'GET');
        const viewState=await request(characterUrl,null,viewer.access_token,'GET');
        assert.equal(ownerState.can_edit,true);assert.equal(viewState.can_edit,false);
        assert.deepEqual(viewState.editable_to,[]);assert.ok(viewState.visible_to.includes(viewer.user_id));
        const initial=await request(locationUrl+'/combat',null,token,'GET');
        const actor=initial.characters.find(c=>c.character_id===attacker.id),target=initial.characters.find(c=>c.character_id===victim.id);
        assert.ok(actor&&target);assert.notEqual(target.controlled_by,viewer.user_id);
        const combat=await request(locationUrl+'/combat/start',{location_character_ids:[actor.location_character_id,target.location_character_id],
            initiator_location_character_id:actor.location_character_id,initiator_first:true},token);
        assert.equal(combat.current_location_character_id,actor.location_character_id);
        await request(locationUrl+'/combat/action',{location_character_id:actor.location_character_id,action_key:'change_facing',facing_x:1,facing_y:0,payment:'action'},token);
        record('synthetic-access',{ownerId:owner.user_id,viewerId:viewer.user_id,victimId:victim.id,attackerId:attacker.id,
            viewerCanEdit:viewState.can_edit,controlledBy:target.controlled_by});
        const version=await(await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();
        protocol=await connect(version.webSocketDebuggerUrl);context=await protocol.call('Target.createBrowserContext');
        const tab=await protocol.call('Target.createTarget',{url:base,browserContextId:context.browserContextId});
        let info;
        for(let i=0;i<100&&!info;i++){info=(await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json()).find(t=>t.id===tab.targetId);if(!info)await pause(100);}
        assert.ok(info,'Собственная вкладка readonly игрока');peer=await connect(info.webSocketDebuggerUrl);
        await peer.call('Page.enable');await peer.call('Runtime.enable');
        const clients=[{client:page,login:owner,role:'owner'},{client:peer,login:viewer,role:'viewer'}];
        const initialUI={};
        for(const entry of clients){
            const {client,login,role}=entry;
            await client.call('Page.navigate',{url:base});await client.until('location.origin==='+JSON.stringify(base),'Собственный origin');
            await client.evaluate(`localStorage.setItem('access_token',${JSON.stringify(login.access_token)});localStorage.setItem('user_id',${JSON.stringify(String(login.user_id))});true`);
            await client.call('Page.navigate',{url:base+api+'/page'});
            await client.until(`typeof openCharacterSheet==='function'&&typeof enterLocation==='function'&&window.isGM===false&&window.socket?.connected&&Number(localStorage.getItem('user_id'))===${login.user_id}`,'Обычный игрок подключён');
            await client.evaluate(`(async()=>{await enterLocation(${location.id})})()`);
            await client.until(`window.locationControls&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Припять загружена');
            const renderer=await client.evaluate(`(()=>{const c=document.querySelector('#location-canvas canvas'),gl=c&&(c.getContext('webgl2')||c.getContext('webgl')),ext=gl?.getExtension('WEBGL_debug_renderer_info');return ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):null})()`);
            assertGpu(renderer);
            await client.evaluate(`(${collector.toString()})(${victim.id},${projectHealth.toString()},${zoneKey.toString()})`);
            await client.evaluate(`(async()=>{await openCharacterSheet(${victim.id})})()`);
            await client.until(`(()=>{const s=${expression(sheetSnapshot)};return s.sheetVisible&&s.fieldCount===1&&s.hp===700&&s.opens===1})()`,'Видимые ОЗ открытого листа');
            const ack=await client.evaluate(`new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Подписка не подтверждена')),10000);socket.emit('join_character',{character_id:${victim.id},token:localStorage.getItem('access_token')},ack=>{clearTimeout(timer);resolve({ok:ack?.ok===true,characterId:ack?.character_id})})})`);
            assert.deepEqual(ack,{ok:true,characterId:victim.id});
            initialUI[role]=await client.evaluate(expression(sheetSnapshot));
            assert.equal(initialUI[role].disabled,role==='viewer');
            record(role+':ready',{renderer,subscription:ack,ui:initialUI[role]});
        }
        const beforeDeny=await request(characterUrl,null,owner.access_token,'GET');
        const denied=await fetch(base+characterUrl,{method:'PUT',headers:{'Content-Type':'application/json',Authorization:'Bearer '+viewer.access_token},
            body:JSON.stringify({name:'Запрещённое изменение',data:{...beforeDeny.data,health:{...beforeDeny.data.health,current:999}}})});
        assert.equal(denied.status,403);await denied.arrayBuffer();
        const socketDenied=await peer.evaluate(`new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Нет ответа readonly save')),10000);socket.emit('update_character_data',{token:localStorage.getItem('access_token'),character_id:${victim.id},updates:{name:'Запрещено'}},ack=>{clearTimeout(timer);resolve({ok:ack?.ok,code:ack?.code})})})`);
        assert.deepEqual(socketDenied,{ok:false,code:403});
        const afterDeny=await request(characterUrl,null,owner.access_token,'GET');assert.deepEqual(afterDeny,beforeDeny);
        record('readonly-denied',{http:denied.status,socket:socketDenied,unchanged:true,snapshot:projectHealth(afterDeny.data)});
        const evidence=async entry=>entry.client.evaluate(expression(readEvidence));
        const barriers=async()=>Object.fromEntries(await Promise.all(clients.map(async e=>[e.role,(await evidence(e)).seq])));
        const uiMatches=async(entry,after)=>{
            await entry.client.until(`(()=>{const s=${expression(sheetSnapshot)};return s.sheetVisible&&s.fieldCount===1&&s.hp===${after.hp}&&s.opens===1&&s.epoch===${initialUI[entry.role].epoch}})()`,'Новые ОЗ без reopen/reload');
            return entry.client.evaluate(expression(sheetSnapshot));
        };
        const hit=async stage=>{
            for(let attempt=1;attempt<=4;attempt++){
                const before=projectHealth((await request(characterUrl,null,owner.access_token,'GET')).data);
                const result=await request(locationUrl+'/combat/action',{location_character_id:actor.location_character_id,
                    action_key:'attack',attack_type:'unarmed',target_character_id:victim.id},token);
                const after=projectHealth((await request(characterUrl,null,owner.access_token,'GET')).data);
                record(stage+':attack',{attempt,before,after,attack:result.attack});
                if(after.hp<before.hp){
                    assertDamage(before,after,result.attack,victim.id);
                    const savedTarget=result.state?.characters?.find(c=>c.character_id===victim.id);
                    assert.ok(savedTarget?.hp_zones&&Object.keys(savedTarget.hp_zones).length,'Сохранены зоны полученного урона');
                    return{before,after,controlKey:zoneKey(savedTarget.hp_zones)};
                }
                assert.equal(after.hp,before.hp,'Промах не меняет пул ОЗ');
            }
            throw Error('За 4 настоящих удара не получен подтверждённый урон; результат не подделывается');
        };
        const firstBarrier=await barriers();
        for(const e of clients)await screenshot(e.client,e.role+'-before');
        const first=await hit('before-revoke');
        const persistedViewer=projectHealth((await request(characterUrl,null,viewer.access_token,'GET')).data);
        assert.deepEqual(persistedViewer,first.after);
        for(const e of clients){
            await e.client.until(`damageEvidence.private.some(x=>x.seq>${firstBarrier[e.role]}&&x.payload.source==='combat'&&x.snapshot?.hp===${first.after.hp}&&x.snapshot.revision>=${first.after.revision})`,'Фактическое событие урона');
            const actual=await evidence(e);assertPrivateEvent(actual,first.after,firstBarrier[e.role]);
            const ui=await uiMatches(e,first.after);record(e.role+':damage-visible',{after:first.after,ui,evidence:actual});
            await screenshot(e.client,e.role+'-first-hit');
        }
        await request(characterUrl+'/visibility',{visible_to:[],editable_to:[]},token,'PUT');
        for(const e of clients)await e.client.until(`damageEvidence.access.some(x=>Array.isArray(x.visibleTo)&&!x.visibleTo.includes(${viewer.user_id}))`,'Доставлено изменение доступа');
        const revokedBarrier=await barriers(); // Viewer не переоформляет подписку: проверяется прежнее членство комнаты.
        const second=await hit('after-revoke');
        for(const e of clients)await e.client.until(`damageEvidence.public.some(x=>x.seq>${revokedBarrier[e.role]}&&x.characterId===${victim.id}&&x.zonesKey===${JSON.stringify(second.controlKey)})`,'Контрольное событие после приватного обновления');
        await page.until(`damageEvidence.private.some(x=>x.seq>${revokedBarrier.owner}&&x.snapshot?.hp===${second.after.hp}&&x.snapshot.revision>=${second.after.revision})`,'Владелец получил новый снимок');
        const ownerUI=await uiMatches(clients[0],second.after);
        const windows=Object.fromEntries(await Promise.all(clients.map(async e=>[e.role,await e.client.evaluate(`(${liveWindow.toString()})(${WINDOW_MS})`)])));
        const ownerEvidence=await evidence(clients[0]),viewerEvidence=await evidence(clients[1]);
        assertRevoked(ownerEvidence,viewerEvidence,second.after,revokedBarrier,windows,second.controlKey);
        const blocked=await fetch(base+characterUrl,{headers:{Authorization:'Bearer '+viewer.access_token}});
        assert.equal(blocked.status,403);await blocked.arrayBuffer();
        const persisted=projectHealth((await request(characterUrl,null,owner.access_token,'GET')).data);
        assert.deepEqual(persisted,second.after);
        assert.ok(ownerEvidence.chat.some(m=>m.message?.includes('Атакующий A15')&&m.message?.includes('Получатель урона A15')),'Фактическое сообщение боя сохранено');
        record('revocation-confirmed',{barriers:revokedBarrier,after:second.after,controlKey:second.controlKey,ownerUI,viewerUI:await peer.evaluate(expression(sheetSnapshot)),
            ownerEvidence,viewerEvidence,windows,viewerGetStatus:blocked.status});
        for(const e of clients)await screenshot(e.client,e.role+'-revoked');
        result.status='READY_FOR_REVIEW';save();
    }catch(error){
        result.status='BLOCKED';result.failure={name:error.name,message:redact(error)};save();
        await screenshot(page,'failure-owner').catch(()=>{});if(peer)await screenshot(peer,'failure-viewer').catch(()=>{});
        throw new Error(redact(error));
    }finally{
        if(context&&protocol)await protocol.call('Target.disposeBrowserContext',{browserContextId:context.browserContextId}).catch(()=>{});
        await peer?.close().catch(()=>{});await protocol?.close().catch(()=>{});
    }
};
Object.assign(module.exports,{projectHealth,zoneKey,loadFixture,assertDamage,assertLiveWindow,assertPrivateEvent,assertRevoked,sheetSnapshot,collector,WINDOW_MS,FIXTURE});
