// MEDICAL-RETRY-BROWSER-01: existing owned SQLite / Chrome / CDP infrastructure.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {loadFixture,waitForDocument}=require('./durable_reload.browser.cjs');
const {assertGpu}=require('./helpers/browser_isolation.cjs');
const copy=x=>JSON.parse(JSON.stringify(x));
function privatePaths(value,at='$',out=[]){
    if(value&&typeof value==='object')for(const [key,child]of Object.entries(value)){
        if(['medical_retry','attack_details'].includes(key))out.push(at+'.'+key);
        privatePaths(child,at+'.'+key,out);
    }return out;
}
function collector(){
    window.med={events:[],http:[],rejections:[]};
    for(const event of ['character_data_updated','combat_state_updated','character_interaction_resolved'])
        socket.on(event,payload=>med.events.push({event,payload,transport:socket.io.engine.transport.name}));
    socket.on('movement_rejected',payload=>med.rejections.push(payload));
    const original=window.fetch;
    window.fetch=async(...args)=>{
        const response=await original(...args),url=new URL(typeof args[0]==='string'?args[0]:args[0].url,location.href);
        if(url.origin===location.origin&&(/medical-procedure$|\/combat\/action$|\/characters\/\d+$/.test(url.pathname))){
            const text=await response.clone().text();let body;try{body=JSON.parse(text)}catch{body={nonJson:true}}
            let requestBody={};try{requestBody=JSON.parse(args[1]?.body||'{}')}catch{}
            med.http.push({path:url.pathname,method:args[1]?.method||'GET',status:response.status,
                operation_id:requestBody.operation_id??null,deferred_action_id:requestBody.deferred_action_id??null,body});
        }return response;
    };return true;
}
function hud(){
    const panel=document.getElementById('combat-status-panel'),body=panel?.querySelector('.combat-hud-body');
    const visible=n=>Boolean(n&&n.getClientRects().length&&getComputedStyle(n).display!=='none');
    return{epoch:performance.timeOrigin,connected:socket.connected,transport:socket.io.engine.transport.name,
        text:body?.innerText||'',visible:visible(body),retryVisible:visible(panel?.querySelector('.combat-must-do-btn'))};
}
module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession})=>{
    assert.ok(safeSession?.verified&&process.env.VTT_SAFE_SCENARIO==='medical-retry'&&process.env.VTT_SAFE_DB_MODE==='owned-file');
    const result={task:'MEDICAL-RETRY-BROWSER-01',runId:safeSession.runId,status:'RUNNING',checks:[],phases:[]};
    const save=()=>fs.writeFileSync(path.join(safeSession.artifacts,'medical-retry.json'),JSON.stringify(result,null,2));
    const record=(stage,value={})=>{result.phases.push({stage,...value});save();};
    const check=(requirement,value={})=>{result.checks.push({requirement,status:'PASS',...value});save();};save();
    let protocol,context,peer;
    const shots=async(label)=>{for(const [role,client]of [['doctor',page],['patient',peer]])if(client)
        fs.writeFileSync(path.join(safeSession.artifacts,`medical-${label}-${role}.png`),Buffer.from((await client.call('Page.captureScreenshot',{format:'png'})).data,'base64'));};
    const database=async(stage)=>{const r=await fetch(base+'/__testing_medical_evidence/'+safeSession.runId);assert.equal(r.status,200);
        const value=await r.json();assert.equal(value.run_id,safeSession.runId);assert.equal(value.testing,true);record(stage,{database:value});return value;};
    const browserRequest=async(client,url,body,method='POST')=>client.evaluate(`(async()=>{const r=await fetch(${JSON.stringify(url)},{method:${JSON.stringify(method)},headers:{'Content-Type':'application/json',Authorization:'Bearer '+localStorage.getItem('access_token')},${body===null?'':`body:JSON.stringify(${JSON.stringify(body)}),`}});return{status:r.status,body:await r.json()}})()`);
    const ok=async(...args)=>{const r=await browserRequest(...args);record('http',{path:args[1],status:r.status,body:r.body});assert.ok(r.status>=200&&r.status<300,JSON.stringify(r));return r.body;};
    const click=async(client,selector)=>{
        await client.call('Page.bringToFront');
        const point=await client.evaluate(`(()=>{const n=document.querySelector(${JSON.stringify(selector)});if(!n||n.disabled)throw Error('Missing enabled UI: '+${JSON.stringify(selector)});n.scrollIntoView({block:'center'});const r=n.getBoundingClientRect();if(!r.width||!r.height)throw Error('Hidden UI');return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);
        for(const type of ['mousePressed','mouseReleased'])await client.call('Input.dispatchMouseEvent',{type,...point,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1});
    };
    try{
        const api='/lobbies/'+lobby.id,{fixture,sha256}=loadFixture();
        const location=await request(api+'/locations',{...fixture,objects:undefined,world_tile_x:8,world_tile_z:8},token);
        const loc=api+'/locations/'+location.id,combat=loc+'/combat',id='medical-retry-browser-01';
        await request(loc+'/objects/batch',{objects:fixture.objects},token);
        record('fixture',{path:'tmp/pripyat-regression.json',sha256,objects:fixture.objects.length,locationId:location.id});
        const controls=await database('initial');assert.equal(controls.receipt_count,0);
        const template=await request(api+'/templates',{name:'Тестовая ампула',description:'Ампула. +5 ХП.',category:'consumable',weight:.1,volume:.1,
            attributes:{consumable:controls.profile,uses:1}},token);
        const item={id:'mr01-ampoule',templateId:template.id+1000000,name:template.name,category:'consumable',weight:.1,volume:.1,quantity:2};
        const baseData=hp=>({health:{current:hp,max:700,stress:0,effects:[]},skills:{physical:{strength:{base:10,bonus:0},will:{base:40,bonus:0}},other:{medicine:{base:10,bonus:0}}},inventory:{pockets:[],backpack:[]},equipment:{}});
        const physicianData=baseData(700);physicianData.inventory.backpack=[item];
        const physician=await request(api+'/characters',{name:'MR01 Врач',data:physicianData},token);
        await request('/auth/register',{username:'mr01-patient',email:'mr01-patient@example.test',password:'synthetic-mr01-password'});
        const patientLogin=await request('/auth/login',{username:'mr01-patient',password:'synthetic-mr01-password'});
        await request(api+'/join',{},patientLogin.access_token);
        const patient=await request(api+'/characters',{name:'MR01 Пациент',data:baseData(100)},patientLogin.access_token);
        await request('/lobbies/characters/'+physician.id+'/visibility',{visible_to:[patientLogin.user_id],editable_to:[]},token,'PUT');
        for(const [person,x]of [[physician,41],[patient,42]])await request(loc+'/spawn_character',{character_id:person.id,tile_x:x,tile_y:48,...(person.id===patient.id?{assign_to_user_id:patientLogin.user_id}:{})},token);
        const initial=await request(combat,null,token,'GET'),actorId=initial.characters.find(c=>c.character_id===physician.id).location_character_id,
            patientId=initial.characters.find(c=>c.character_id===patient.id).location_character_id;
        await request(combat+'/start',{location_character_ids:[actorId,patientId],initiator_location_character_id:actorId,initiator_first:true},token);
        const version=await(await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();protocol=await connect(version.webSocketDebuggerUrl);
        context=await protocol.call('Target.createBrowserContext');
        const target=await protocol.call('Target.createTarget',{url:base,browserContextId:context.browserContextId});
        const targetInfo=(await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json()).find(t=>t.id===target.targetId);assert.ok(targetInfo);
        peer=await connect(targetInfo.webSocketDebuggerUrl);await peer.call('Page.enable');await peer.call('Runtime.enable');
        await waitForDocument(peer,{href:base,label:'patient origin'});
        await peer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(patientLogin.access_token)});localStorage.setItem('user_id',${JSON.stringify(String(patientLogin.user_id))});true`);
        const clients=[page,peer],href=base+api+'/page?location='+location.id;
        const ready=async(client)=>{
            await client.until(`window.socket?.connected&&window.currentLocationId===${location.id}&&window.locationControls&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'medical scene ready');
            const gpu=await client.evaluate(`(()=>{const c=document.querySelector('#location-canvas canvas'),gl=c&&(c.getContext('webgl2')||c.getContext('webgl')),e=gl?.getExtension('WEBGL_debug_renderer_info');return e?gl.getParameter(e.UNMASKED_RENDERER_WEBGL):null})()`);assertGpu(gpu);
            await client.evaluate(`(${collector.toString()})()`);
            for(const sheetId of [physician.id,patient.id]){
                const ack=await client.evaluate(`new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('join_character timeout')),8000);socket.emit('join_character',{character_id:${sheetId},token:localStorage.getItem('access_token')},r=>{clearTimeout(timer);resolve(r)})})`);assert.equal(ack.ok,true);
            }
            if(await client.evaluate(`document.querySelector('#combat-status-panel .combat-hud-collapse-btn')?.getAttribute('aria-expanded')==='false'`))await click(client,'#combat-status-panel .combat-hud-collapse-btn');
            record('client-ready',{gpu,hud:await client.evaluate(`(${hud.toString()})()`)});
        };
        for(const client of clients){const epoch=await client.evaluate('performance.timeOrigin');await client.call('Page.navigate',{url:href});await waitForDocument(client,{href,afterEpoch:epoch,label:'medical initial scene'});await ready(client);}
        const consent=await ok(page,loc+'/character-interactions',{actor_location_character_id:actorId,target_character_id:patient.id,kind:'treatment'});
        assert.equal(consent.status,'pending');
        await ok(peer,api+'/character-interactions/'+consent.id+'/response',{decision:'accept'});
        check('consent');
        const state=()=>request(combat,null,token,'GET');
        let current=(await state()).current_character;
        if(current.action_points_current>1)await ok(page,combat+'/spend',{location_character_id:actorId,action_points:current.action_points_current-1});
        const savedItem=(await request('/lobbies/characters/'+physician.id,null,token,'GET')).data.inventory.backpack[0];
        const application={kind:'self',actionPoints:1},source={path:['inventory','backpack',0],snapshot:savedItem};
        const selection={source,application,target_character_id:patient.id,treatment_request_id:consent.id,server_authoritative:true};
        await ok(page,combat+'/spend',{location_character_id:actorId,action_points:3,allow_deferred:true,pending_action_id:id,pending_action_label:'Тестовая ампула',consumable_selection:selection});
        const paying=await database('paying');assert.equal(paying.deferred.status,'paying');assert.equal(paying.deferred.remaining,2);assert.equal(paying.receipt_count,0);assert.equal(paying.draws.length,0);
        assert.equal(paying.characters.find(c=>c.id===patient.id).movement_locked,true);
        for(const client of clients){await client.until(`window.locationCombatState?.characters?.some(c=>c.character_id===${physician.id}&&c.deferred_action?.remaining_action_points===2)`,'both clients debt 2');}
        await shots('paying');
        const rejectMove=async(label)=>{
            const from=await peer.evaluate('med.rejections.length');
            await peer.evaluate(`socket.emit('move_in_location',{token:localStorage.getItem('access_token'),location_id:${location.id},character_id:${patient.id},x:43,y:48,movement_mode:'walk',floor_level:0});true`);
            await peer.until(`med.rejections.length>${from}`,'patient movement rejected');const rejection=await peer.evaluate('med.rejections.at(-1)');
            assert.match(rejection.message,/treatment is in progress/);assert.equal(rejection.x,42);assert.equal(rejection.y,48);record(label,{rejection});
        };
        await rejectMove('patient-locked-paying');check('payment-debt-and-patient-lock');
        // Actual reload while debt and lock exist; no fresh fake page state.
        for(const client of clients){const before=await client.evaluate(`(${hud.toString()})()`);await client.call('Page.reload',{ignoreCache:true});
            await waitForDocument(client,{href,afterEpoch:before.epoch,label:'paying reload'});await ready(client);
            await client.until(`window.locationCombatState?.characters?.some(c=>c.character_id===${physician.id}&&c.deferred_action?.remaining_action_points===2)`,'debt survives reload');}
        await rejectMove('patient-locked-after-reload');check('debt-lock-reload');
        await ok(page,combat+'/end_turn',{location_character_id:actorId});
        await ok(peer,combat+'/end_turn',{location_character_id:patientId});
        // Обновление боя запускает штатное завершение действия; наблюдаем его запрос без конкурентного ручного POST.
        await page.until(`med.http.some(r=>r.path.endsWith('/medical-procedure')&&r.method==='POST'&&r.operation_id===${JSON.stringify(id)}&&r.deferred_action_id===${JSON.stringify(id)}&&r.status===200)`,'automatic deferred medical procedure');
        const automatic=await page.evaluate(`med.http.find(r=>r.path.endsWith('/medical-procedure')&&r.method==='POST'&&r.operation_id===${JSON.stringify(id)}&&r.deferred_action_id===${JSON.stringify(id)}&&r.status===200)`);
        assert.ok(automatic);record('automatic-medical-resume',{status:automatic.status,operation_id:automatic.operation_id,deferred_action_id:automatic.deferred_action_id,body:automatic.body});
        const failure=automatic.body;
        assert.equal(failure.medical_result.success,false);assert.equal(failure.medical_result.roll,1);assert.equal(failure.medical_result.difficulty,4);assert.deepEqual(privatePaths(failure),[]);
        const failed=await database('failed');const doctor=c=>c.characters.find(p=>p.id===physician.id),patientDB=c=>c.characters.find(p=>p.id===patient.id);
        assert.equal(failed.deferred.status,'completed');assert.equal(failed.deferred.remaining,0);assert.equal(failed.deferred.payload.medicine_roll,1);assert.equal(failed.receipt_count,1);assert.equal(failed.draws.length,1);
        const trusted=copy(doctor(failed).data.health.combatMeta.mustDoRetry);assert.equal(trusted.kind,'medical');assert.equal(trusted.difficulty,4);
        assert.equal(trusted.medical_retry.server_authoritative,true);assert.equal(trusted.medical_retry.target_character_id,patient.id);
        assert.equal(doctor(failed).data.inventory.backpack[0].quantity,1);assert.equal(patientDB(failed).data.health.current,100);assert.equal(failed.receipt_count,1);assert.equal(failed.deferred.status,'completed');assert.equal(patientDB(failed).movement_locked,false);
        for(const client of clients){await client.until(`med.events.some(e=>e.event==='character_data_updated'&&e.payload.source==='medical_procedure'&&e.payload.character_id===${physician.id})`,'public medical event');
            const events=await client.evaluate(`med.events.filter(e=>e.event==='character_data_updated'&&e.payload.source==='medical_procedure')`);
            assert.ok(events.length>=1);for(const e of events)assert.deepEqual(privatePaths(e.payload),[]);
            await client.until(`window.locationCombatState?.characters?.some(c=>c.character_id===${physician.id}&&c.must_do_retry?.difficulty===4)`,'retry summary synchronized');record('failed-client',{events,hud:await client.evaluate(`(${hud.toString()})()`)});}
        check('failed-procedure-and-server-retry');check('medical-http-and-events-public');check('resource-once-on-failure');
        // Save the exact public medical response through the application's normal saver.
        const publicData=copy(failure.actor_data);publicData.notes='MR01 public snapshot save';
        // Запоминаем медицинский HTTP-снимок так же, как requestJson. Обычный GET
        // дополнительно проверяет сохранение исходной публичной версии после обновления листа.
        const saved=await page.evaluate(`(async()=>{const {Server}=await import('/static/js/api.js');const {rememberCharacterSnapshots}=await import('/static/js/characterPersistence.js');rememberCharacterSnapshots(${JSON.stringify(failure)});await Server.getCharacter(${physician.id});return Server.updateCharacter(${physician.id},{data:${JSON.stringify(publicData)}})})()`);assert.ok(saved.data);
        const afterSave=await database('public-sheet-save');assert.deepEqual(doctor(afterSave).data.health.combatMeta.mustDoRetry,trusted);assert.equal(doctor(afterSave).data.notes,publicData.notes);
        // Also exercise the rendered sheet autosave using its real notes field.
        const beforeSheet=await page.evaluate(`({modal:document.getElementById('character-sheet-modal')?.style.display,open:document.body.classList.contains('character-sheet-open'),notifications:[...document.querySelectorAll('.notification')].map(n=>n.textContent).slice(-3)})`);record('before-sheet-open',beforeSheet);
        await page.evaluate(`window.openCharacterSheet(${physician.id},'basic').then(()=>true)`);await page.until(`document.querySelector('#sheet-tabs [data-tab="notes"]')`,'notes tab');await click(page,'#sheet-tabs [data-tab="notes"]');
        await page.evaluate(`(()=>{const n=document.querySelector('#sheet-content [name="notes"]');n.value='MR01 actual sheet autosave';n.dispatchEvent(new Event('input',{bubbles:true}));return true})()`);
        await page.until(`med.http.some(r=>r.method==='PUT'&&r.status===200&&r.body.data?.notes==='MR01 actual sheet autosave')`,'actual sheet autosave HTTP');
        await page.evaluate(`(async()=>{const s=await import('/static/js/characterSheet.js');s.closeCharacterSheet()})()`);
        for(const client of clients){const before=await client.evaluate(`(${hud.toString()})()`);await client.call('Page.reload',{ignoreCache:true});
            await waitForDocument(client,{href,afterEpoch:before.epoch,label:'failed retry reload'});await ready(client);
            await client.until(`window.locationCombatState?.characters?.some(c=>c.character_id===${physician.id}&&c.must_do_retry?.difficulty===4)`,'original retry after reload');
            await client.evaluate(`openCharacterSheet(${physician.id})`);await client.until(`document.querySelector('#sheet-content [name="health.current"]')`,'reopened sheet');
            const snap=await client.evaluate(`({notes:document.querySelector('#sheet-content [name="notes"]')?.value,epoch:performance.timeOrigin})`);assert.equal(snap.notes,'MR01 actual sheet autosave');record('sheet-reloaded',{snapshot:snap});
            await client.evaluate(`(async()=>{const s=await import('/static/js/characterSheet.js');s.closeCharacterSheet()})()`);
            for(const sheetId of [physician.id,patient.id])await client.evaluate(`new Promise(resolve=>socket.emit('join_character',{character_id:${sheetId},token:localStorage.getItem('access_token')},resolve))`);
        }
        const reloaded=await database('retry-after-reload');assert.deepEqual(doctor(reloaded).data.health.combatMeta.mustDoRetry,trusted);assert.equal(reloaded.receipt_count,1);assert.deepEqual(reloaded.draws,failed.draws);
        const apBefore=(await state()).current_character.action_points_current;
        await shots('failed-reloaded');check('public-sheet-save-actual-autosave-and-reload');check('original-procedure-dc-and-two-clients');
        // User-facing retry button; HTTP interception observes the application's own request.
        await click(page,'#combat-status-panel .combat-must-do-btn');
        await page.until(`med.http.some(r=>r.path.endsWith('/combat/action')&&r.body.must_do_it?.kind==='medical')`,'UI medical retry HTTP');
        const retried=await page.evaluate(`med.http.find(r=>r.path.endsWith('/combat/action')&&r.body.must_do_it?.kind==='medical')`);record('ui-retry-http',retried);
        assert.equal(retried.status,200);assert.deepEqual(privatePaths(retried.body),[]);
        const detail=retried.body.must_do_it;for(const key of ['kind','name','skill_path','difficulty'])assert.deepEqual(detail[key],trusted[key]);
        assert.equal(detail.check.success,true);assert.equal(detail.check.roll,20);assert.equal(detail.check.difficulty,4);assert.equal(detail.medical_result.target_character_id,patient.id);
        const completed=await database('completed');assert.equal(patientDB(completed).data.health.current,105);assert.deepEqual(doctor(completed).data.inventory,doctor(failed).data.inventory);
        assert.equal(completed.receipt_count,1);assert.deepEqual(completed.deferred,failed.deferred);assert.equal(completed.draws.length,2);assert.ok(!doctor(completed).data.health.combatMeta.mustDoRetry);
        assert.equal((await state()).current_character.action_points_current,apBefore);
        for(const client of clients){await client.until(`med.events.some(e=>e.event==='character_data_updated'&&e.payload.character_id===${patient.id}&&e.payload.updates?.data?.health?.current===105)`,'patient success event');
            await client.until(`window.locationCombatState?.characters?.some(c=>c.character_id===${physician.id}&&!c.must_do_retry)`,'retry cleared both clients');
            await client.evaluate(`openCharacterSheet(${patient.id})`);await client.until(`document.querySelector('#sheet-content [name="health.current"]')?.value==='105'`,'visible patient HP105');
            record('completed-client',{events:await client.evaluate(`med.events`),hud:await client.evaluate(`(${hud.toString()})()`)});}
        await shots('completed');check('native-ui-retry-original-parameters');check('single-effect-no-second-resource-or-ap');check('two-client-success-and-cleared-retry');
        const replay=await browserRequest(page,combat+'/action',{location_character_id:actorId,action_key:'must_do_it'});assert.equal(replay.status,400);record('retry-replay',replay);
        const replayBody={operation_id:id,deferred_action_id:id,target_character_id:patient.id,source,application,combat_location_id:location.id,actor_location_character_id:actorId,interaction_request_id:consent.id};
        const replayMedical=await browserRequest(page,'/lobbies/characters/'+physician.id+'/medical-procedure',replayBody);record('medical-replay',replayMedical);assert.equal(replayMedical.status,403,'Completed consent rejects an other-patient medical replay before completion claim');
        const final=await database('after-replays');
        const finalComparable=copy(final),completedComparable=copy(completed);
        const finalDoctor=finalComparable.characters.find(c=>c.id===physician.id),completedDoctor=completedComparable.characters.find(c=>c.id===physician.id);
        const journalBefore=completedDoctor.data.health.combatMeta.actionJournal,journalAfter=finalDoctor.data.health.combatMeta.actionJournal;
        assert.equal(journalAfter.length,journalBefore.length+1,'В журнал добавлен только отклонённый повтор');
        assert.deepEqual(journalAfter.slice(0,-1),journalBefore,'Прежние записи журнала не изменились');
        const rejected=journalAfter.at(-1);
        assert.deepEqual(rejected,{id:rejected.id,timestamp:rejected.timestamp,action:'must_do_it',label:'Должен это сделать',
            location_id:location.id,status:'rejected',action_points:0,movement_points:0,free_actions:0,
            reason:replay.body.error.message});
        assert.equal(replay.body.error.message,'Describe the check approved by the GM');
        assert.ok(!journalBefore.some(entry=>entry.id===rejected.id));
        assert.equal(finalDoctor.revision,completedDoctor.revision+1,'Запись отказа повышает ревизию персонажа ровно на один');
        journalAfter.pop();finalDoctor.revision=completedDoctor.revision;
        assert.deepEqual(finalComparable,completed,'Повторы не меняют прочие данные персонажей, здоровье, оплату, расход и RNG');
        assert.equal((await state()).current_character.action_points_current,apBefore);check('replay-rejected-unchanged-db-and-rng');
        for(const client of clients)assert.equal(client.events.has('Runtime.exceptionThrown'),false,
            'Фоновое завершение лечения и открытие листов не вызывают исключений JavaScript');
        result.status='PASS';save();
    }catch(error){result.status=error.name==='AssertionError'?'FAIL':'BLOCKED';result.failure={name:error.name,message:String(error.message).replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g,'[test-token]')};save();await shots('failure').catch(()=>{});throw error;}
    finally{if(context&&protocol)await protocol.call('Target.disposeBrowserContext',{browserContextId:context.browserContextId}).catch(()=>{});await peer?.close().catch(()=>{});await protocol?.close().catch(()=>{});}
};
Object.assign(module.exports,{privatePaths});
