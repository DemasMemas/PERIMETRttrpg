// D30/S75: настоящий Page.reload между частичными платежами. Импорт не запускает приложение.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {createHash}=require('node:crypto');
const {assertGpu}=require('./helpers/browser_isolation.cjs');
const FIXTURE=path.resolve(__dirname,'../tmp/pripyat-regression.json');
const CASES=Object.freeze(['narrative','attack','clear-jam','equip','general']);
const PASSWORD='synthetic-durable-password';
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const copy=value=>JSON.parse(JSON.stringify(value));
function loadFixture(io=fs){
    const stat=io.lstatSync(FIXTURE);assert.ok(stat.isFile()&&!stat.isSymbolicLink());
    assert.equal(io.realpathSync(FIXTURE),FIXTURE,'Фиксированная Припять без перенаправления');
    const bytes=io.readFileSync(FIXTURE),fixture=JSON.parse(bytes.toString('utf8'));
    assert.ok(fixture.name?.includes('Припять')&&fixture.grid_width===72&&fixture.grid_height===72);
    assert.ok(Array.isArray(fixture.objects)&&fixture.objects.length>0);
    return{fixture,sha256:createHash('sha256').update(bytes).digest('hex')};
}
function summary(state,actorId){return state.characters.find(c=>c.location_character_id===actorId)?.deferred_action;}
function assertSummary(actual,expected){
    assert.ok(actual);for(const key of ['id','action_key','label','status','remaining_action_points'])assert.equal(actual[key],expected[key],key);
    assert.ok(actual.status==='paying'?actual.remaining_action_points>0:actual.status==='ready'&&actual.remaining_action_points===0);
}
function hudSnapshot(id){
    const visible=node=>Boolean(node&&node.getClientRects().length&&getComputedStyle(node).display!=='none'&&getComputedStyle(node).visibility!=='hidden');
    const button=[...document.querySelectorAll('#combat-status-panel .combat-deferred-cancel')].find(node=>node.dataset.action===id);
    const card=button?.parentElement;
    return{epoch:performance.timeOrigin,url:location.pathname+location.search,id:button?.dataset.action||null,
        text:card?.innerText||'',visible:visible(button)&&visible(card),connected:window.socket?.connected===true};
}
function assertReloadRecovery(before,after,expected,actorName){
    assert.ok(Number.isFinite(before.epoch)&&Number.isFinite(after.epoch)&&after.epoch>before.epoch,'Подтверждён новый документ после Page.reload');
    assert.equal(after.url,before.url,'Сохранён адрес реальной игровой сцены');
    for(const item of [before,after]){
        assert.ok(item.visible&&item.connected,'BLOCKED_UI: видимый восстановленный HUD и живое соединение');
        assert.equal(item.id,expected.id);assert.ok(item.text.includes(actorName)&&item.text.includes(expected.label),'BLOCKED_UI: имя персонажа и действия не восстановлены');
        assert.ok(item.text.includes(`Осталось оплатить: ${expected.remaining_action_points} ОД`),'BLOCKED_UI: не виден точный остаток долга');
    }
}
function assertPayment(previous,next,apMax){
    assert.ok(previous.status==='paying'&&previous.remaining_action_points>0);
    assert.equal(next.id,previous.id);assert.equal(next.action_key,previous.action_key);assert.equal(next.label,previous.label);
    assert.equal(next.remaining_action_points,Math.max(0,previous.remaining_action_points-apMax),'Долг уменьшается штатным началом хода');
    assert.equal(next.status,next.remaining_action_points>0?'paying':'ready');
}
function projectItems(data){return{weapons:data.weapons||[],inventory:data.inventory||{},equipment:data.equipment||{},radiation:Number(data.health?.radiation||0)};}
function assertEffect(kind,before,after,result,ids){
    if(kind==='narrative'){
        assert.equal(result.action,'narrative_action');assert.equal(result.narrative_action.name,ids.narrativeName);
        assert.equal(result.narrative_action.action_points,ids.total);assert.equal(result.narrative_action.roll_required,false);
    }else if(kind==='attack'){
        assert.equal(result.action,'attack');assert.equal(result.attack.weapon_index,0);
        assert.equal(result.attack.target_character_id,ids.targetId);assert.equal(result.attack.fire_mode,'aimed');
        assert.equal(result.attack.target_zone,'chest');assert.equal(result.attack.shot_count,1);assert.equal(result.attack.action_points,ids.total);
        assert.ok(result.attack.results?.length>0&&result.attack.results.every(hit=>hit.target_character_id===ids.targetId));
        assert.equal(Number(after.weapons[0].ammo),Number(before.weapons[0].ammo)-1,'Ровно один выстрел');
        assert.deepEqual(after.weapons[1],before.weapons[1],'Подставное оружие не использовано');
    }else if(kind==='clear-jam'){
        assert.equal(result.action,'clear_weapon_jam');assert.equal(result.attack,null);
        assert.equal(result.clear_weapon_jam.weapon_index,0);assert.equal(result.clear_weapon_jam.result,10);assert.equal(result.clear_weapon_jam.action_points,ids.total);
        const expected=copy(before.weapons[0]);delete expected.jam;delete expected.jams;
        assert.deepEqual(after.weapons[0],expected);assert.deepEqual(after.weapons[1],before.weapons[1]);
    }else if(kind==='equip'){
        assert.equal(result.action,'change_equipment');assert.equal(result.equipment_change.operation,'equip');
        assert.equal(result.equipment_change.slot,'helmet');assert.equal(result.equipment_change.action_points,ids.total);
        assert.equal(after.equipment.helmet?.id,ids.itemId);assert.equal(after.equipment.helmet.durability,27);
        assert.deepEqual(after.inventory.backpack.map(item=>item.id),[ids.decoyItemId]);
        assert.equal(after.equipment.helmet.customNote,'Сохранить метку');
    }else if(kind==='general'){
        assert.equal(result.consumable_result.item_name,ids.itemName);
        assert.equal(result.consumable_result.payment_details.total_action_points,ids.total);
        assert.equal(after.radiation,before.radiation+1);
        assert.equal(after.inventory.backpack[0].id,ids.itemId);assert.equal(after.inventory.backpack[0].quantity,1);
        assert.equal(after.inventory.backpack[0].customNote,'Сохранить метку');
        assert.deepEqual(after.inventory.backpack[1],before.inventory.backpack[1]);
    }else assert.fail('Неизвестный фиксированный случай');
}
function observerCollector(){
    window.durableChat={events:[],history:[],historyVersion:0,disconnects:0};
    const project=m=>({username:m.username,message:m.message,timestamp:m.timestamp});
    socket.on('new_message',m=>durableChat.events.push(project(m)));
    socket.on('chat_history',messages=>{durableChat.history=messages.map(project);durableChat.historyVersion++;});
    socket.on('disconnect',()=>durableChat.disconnects++);return true;
}
function rifle(id,jammed=false){
    const weapon={id,name:'Винтовка '+id,category:'weapon',weight:1,ergonomics:0,accuracy:0,range:20,
        durability:100,maxDurability:100,ammo:3,fireModes:{single_shot_options:[1]},
        installedMagazine:{id:id+'-mag',ammo:[{id:id+'-rounds',name:'5.56x45',quantity:3,category:'ammo',
            attributes:{caliber:'5.56x45',damage:10,armor_piercing:0}}]}};
    if(jammed){const jam={id:id+'-jam',result:10,label:'Повреждены курок, затвор и рама',fix_ap:8,durability_loss:10,blocks_fire:true};weapon.jam=copy(jam);weapon.jams=[copy(jam)];}
    return weapon;
}
function baseData(){return{health:{current:700,max:700,effects:[],radiation:0},inventory:{pockets:[],backpack:[]},equipment:{},
    skills:{physical:{strength:{base:10,bonus:0},will:{base:40,bonus:0},shooting:{base:10,bonus:0}}}};}
// Повтор разрешён только для чтения Runtime в окне намеренной навигации.
function documentSnapshot(){return{href:location.href,epoch:performance.timeOrigin,readyState:document.readyState};}
function isTransientNavigationError(error){
    let value;try{value=JSON.parse(error.message);}catch{return false;}
    return value?.code===-32000&&value.message==='Inspected target navigated or closed';
}
function documentMatches(value,href,afterEpoch){
    return Boolean(value&&value.href===new URL(href).href&&Number.isFinite(value.epoch)&&value.epoch>0
        &&(afterEpoch===undefined||value.epoch>afterEpoch)&&['interactive','complete'].includes(value.readyState));
}
async function waitForDocument(client,{href,afterEpoch,label,timeoutMs=15000},clock={now:()=>performance.now(),pause}){
    assert.ok(Number.isInteger(timeoutMs)&&timeoutMs>0&&timeoutMs<=15000);
    if(afterEpoch!==undefined)assert.ok(Number.isFinite(afterEpoch)&&afterEpoch>0);
    const start=clock.now(),deadline=start+timeoutMs;let attempts=0,transientErrors=0,last=null;
    const diagnostic=()=>({label,attempts,transientErrors,elapsedMs:clock.now()-start,
        expectedUrlSeen:last?.href===new URL(href).href,epoch:last?.epoch??null,readyState:last?.readyState??null});
    while(clock.now()<deadline){
        let timer;attempts++;
        try{
            // Даже один зависший read ограничен общим deadline; timeout не повторяется.
            last=await Promise.race([client.evaluate(`(${documentSnapshot.toString()})()`),
                new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Истекло ожидание Runtime документа: '+JSON.stringify(diagnostic()))),Math.max(1,deadline-clock.now()));})]);
            if(documentMatches(last,href,afterEpoch))return{...diagnostic(),snapshot:last};
        }catch(error){
            if(!isTransientNavigationError(error))throw error;
            transientErrors++;
        }finally{clearTimeout(timer);}
        const remaining=deadline-clock.now();if(remaining>0)await clock.pause(Math.min(100,remaining));
    }
    throw Error('Документ не подтвердил готовность: '+JSON.stringify(diagnostic()));
}
function cleanError(error){return String(error?.message||error).replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g,'[тестовый токен]').replaceAll(PASSWORD,'[тестовый пароль]');}
module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession})=>{
    assert.ok(safeSession?.verified&&process.env.VTT_SAFE_SCENARIO==='durable-reload'&&process.env.VTT_SAFE_DB_MODE==='owned-file');
    const result={task:'BROWSER-DURABLE-RELOAD',runId:safeSession.runId,status:'RUNNING',phases:[],cases:[],navigation:[],
        limits:['Медицинский d20/DC и lock второго клиента не проверяются этим пакетом.',
            'Число внутренних строк receipt не открыто HTTP API; проверяются успешное применение, повтор409 и полная неизменность наблюдаемых данных.']};
    const save=()=>fs.writeFileSync(path.join(safeSession.artifacts,'durable-reload.json'),JSON.stringify(result,null,2));
    const record=(stage,value={})=>{result.phases.push({stage,...value});save();};save();
    const screenshot=async(label)=>fs.writeFileSync(path.join(safeSession.artifacts,'durable-'+label+'.png'),Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    let protocol,context,observer;
    const navigateDocument=async(client,href,label,{reload=false,afterEpoch}={})=>{
        // Старый документ читается один раз: неизвестные ошибки и сбой вне навигации не повторяются.
        const previousEpoch=afterEpoch??(await client.evaluate(`(${documentSnapshot.toString()})()`)).epoch;
        const entry={label,method:reload?'Page.reload':'Page.navigate',status:'STARTED',afterEpoch:previousEpoch};
        result.navigation.push(entry);save();
        try{
            const response=await client.call(entry.method,reload?{ignoreCache:true}:{url:href});
            assert.ok(!response?.errorText,response?.errorText);
            const ready=await waitForDocument(client,{href,afterEpoch:previousEpoch,label});
            Object.assign(entry,ready,{status:'READY'});save();return ready.snapshot;
        }catch(error){entry.status='BLOCKED';entry.failure=cleanError(error);save();throw error;}
    };
    try{
        const {fixture,sha256}=loadFixture(),api=`/lobbies/${lobby.id}`;
        let loc=await request(api+'/locations',{...fixture,objects:undefined,world_tile_x:8,world_tile_z:8},token);
        let locUrl=api+'/locations/'+loc.id,combatUrl=locUrl+'/combat',pageUrl=base+api+'/page?location='+loc.id;
        await request(locUrl+'/objects/batch',{objects:fixture.objects},token);
        record('pripyat-mounted',{locationId:loc.id,objects:fixture.objects.length,sha256});
        await request('/auth/register',{username:'durable-observer',email:'durable-observer@example.test',password:PASSWORD});
        const login=await request('/auth/login',{username:'durable-observer',password:PASSWORD});await request(api+'/join',{},login.access_token);
        const version=await(await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();
        protocol=await connect(version.webSocketDebuggerUrl);context=await protocol.call('Target.createBrowserContext');
        const tab=await protocol.call('Target.createTarget',{url:base,browserContextId:context.browserContextId});
        let info;for(let i=0;i<100&&!info;i++){info=(await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json()).find(t=>t.id===tab.targetId);if(!info)await pause(100);}
        assert.ok(info);observer=await connect(info.webSocketDebuggerUrl);await observer.call('Page.enable');await observer.call('Runtime.enable');
        await waitForDocument(observer,{href:base,label:'Документ собственного Target.createTarget'});
        await observer.evaluate(`localStorage.setItem('access_token',${JSON.stringify(login.access_token)});localStorage.setItem('user_id',${JSON.stringify(String(login.user_id))});true`);
        await navigateDocument(observer,base+api+'/page','Переход наблюдателя в комнату');
        await observer.until('window.socket?.connected&&window.isGM===false','Обычный наблюдатель чата');
        await observer.evaluate(`(${observerCollector.toString()})()`);
        const history=async()=>{
            const old=await observer.evaluate('durableChat.historyVersion');
            await observer.evaluate(`socket.emit('authenticate',{token:localStorage.getItem('access_token'),lobby_id:${lobby.id}});true`);
            await observer.until(`durableChat.historyVersion>${old}`,'Сохранённая история сообщений');
            return observer.evaluate('({history:durableChat.history,events:durableChat.events,connected:socket.connected,disconnects:durableChat.disconnects})');
        };
        await history();
        const getState=()=>request(combatUrl,null,token,'GET');
        const getSheet=id=>request('/lobbies/characters/'+id,null,token,'GET');
        const sceneReady=async()=>{
            await page.until(`window.socket?.connected&&window.currentLocationId===${loc.id}&&window.locationControls&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Припять и бой загружены');
            const renderer=await page.evaluate(`(()=>{const c=document.querySelector('#location-canvas canvas'),gl=c&&(c.getContext('webgl2')||c.getContext('webgl')),ext=gl?.getExtension('WEBGL_debug_renderer_info');return ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):null})()`);assertGpu(renderer);return renderer;
        };
        const readHud=async(id)=>{
            const toggle=await page.evaluate(`document.querySelector('#combat-status-panel .combat-hud-collapse-btn')?.getAttribute('aria-expanded')==='false'`);
            if(toggle)await page.evaluate(`document.querySelector('#combat-status-panel .combat-hud-collapse-btn').click();true`);
            await page.until(`(${hudSnapshot.toString()})(${JSON.stringify(id)}).visible`,'BLOCKED_UI: карточка долга не видна');
            return page.evaluate(`(${hudSnapshot.toString()})(${JSON.stringify(id)})`);
        };
        const offline=async()=>{
            const current=await page.evaluate(`(${documentSnapshot.toString()})()`);
            if(!documentMatches(current,'about:blank'))await navigateDocument(page,'about:blank','Клиент покинул сцену',{afterEpoch:current.epoch});
        };

        for(const [index,kind]of CASES.entries()){
            const outcome={kind,status:'RUNNING',checks:[]};result.cases.push(outcome);save();
            const check=(stage,data={})=>{outcome.checks.push({stage,...data});save();};
            try{
                await offline();
                if(index){
                    loc=await request(api+'/locations',{...fixture,objects:undefined,world_tile_x:8+index,world_tile_z:8},token);
                    locUrl=api+'/locations/'+loc.id;combatUrl=locUrl+'/combat';pageUrl=base+api+'/page?location='+loc.id;
                    await request(locUrl+'/objects/batch',{objects:fixture.objects},token);
                }
                outcome.locationId=loc.id;const actorName='Исполнитель '+kind+' D30',id='durable-'+kind;
                const ids={narrativeName:'Ремонт двери D30',itemId:'durable-'+kind+'-item',decoyItemId:'durable-'+kind+'-decoy'};
                let data=baseData();
                if(kind==='attack'||kind==='clear-jam'){
                    data.weapons=[rifle(kind+'-original',kind==='clear-jam'),rifle(kind+'-other',kind==='clear-jam')];data.activeWeaponIndex=0;
                }
                if(kind==='equip'||kind==='general'){
                    const attributes=kind==='equip'?{}:{uses:1,consumable:{editor_version:1,direct:{action_points_cost:3,radiation_delta:1},effects:[],modifiers:[]}};
                    const template=await request(api+'/templates',{name:kind==='equip'?'Шлем D30':'Простой расходник S75',category:kind==='equip'?'helmet':'consumable',weight:kind==='equip'?2.1:.1,volume:1,attributes},token);
                    ids.itemName=template.name;
                    const item={id:ids.itemId,templateId:template.id+1000000,name:template.name,category:template.category,weight:template.weight,
                        attributes:copy(attributes),quantity:kind==='equip'?1:2,durability:27,customNote:'Сохранить метку'};
                    const decoy={...copy(item),id:ids.decoyItemId,name:'Подставной предмет '+kind};
                    data.inventory.backpack=[item,decoy];
                }
                const actorSheet=await request(api+'/characters',{name:actorName,data},token);
                const targetSheet=await request(api+'/characters',{name:'Исходная цель '+kind+' D30',data:baseData()},token);
                const decoySheet=await request(api+'/characters',{name:'Подставная цель '+kind+' D30',data:baseData()},token);
                for(const [sheet,x,y]of [[actorSheet,41,48],[targetSheet,42,48],[decoySheet,42,49]])
                    await request(locUrl+'/spawn_character',{character_id:sheet.id,tile_x:x,tile_y:y},token);
                let state=await getState(),actor=state.characters.find(c=>c.character_id===actorSheet.id),target=state.characters.find(c=>c.character_id===targetSheet.id);
                assert.ok(actor&&target);const actorId=actor.location_character_id,targetId=target.location_character_id;
                state=await request(combatUrl+'/start',{location_character_ids:[actorId,targetId],initiator_location_character_id:actorId,initiator_first:true},token);
                assert.equal(state.current_location_character_id,actorId);
                const act=payload=>request(combatUrl+'/action',{location_character_id:actorId,...payload},token);
                if(kind==='attack')await act({action_key:'change_facing',facing_x:1,facing_y:0,payment:'action'});
                actor=(await getState()).characters.find(c=>c.location_character_id===actorId);
                const reserve=kind==='attack'?2:1;
                assert.ok(actor.action_points_current>=reserve);
                if(actor.action_points_current>reserve)await request(combatUrl+'/spend',{location_character_id:actorId,action_points:actor.action_points_current-reserve},token);
                if(kind==='attack')await act({action_key:'aim',weapon_index:0,target_character_id:targetSheet.id});
                actor=(await getState()).characters.find(c=>c.location_character_id===actorId);
                assert.equal(actor.action_points_current,1,'Ресурсы уменьшены штатными действиями до начала операции');
                const initialItems=projectItems((await getSheet(actorSheet.id)).data),initialTarget=(await getSheet(targetSheet.id)).data;
                const initialDecoy=(await getSheet(decoySheet.id)).data;
                ids.targetId=targetSheet.id;
                let startBody,selection,source;
                if(kind==='narrative')startBody={action_key:'narrative_action',narrative_action_name:ids.narrativeName,narrative_roll_required:false,action_points:13};
                if(kind==='attack')startBody={action_key:'attack',weapon_index:0,target_character_id:targetSheet.id,target_zone:'chest',
                    fire_mode:'aimed',shot_count:1,volley_count:1,action_points:actor.weapon_ergonomics[0].aimed_shot_action_points};
                if(kind==='clear-jam')startBody={action_key:'clear_weapon_jam',weapon_index:0};
                if(kind==='equip')startBody={action_key:'change_equipment',equipment_operation:'equip',equipment_slot:'helmet',item_path:['inventory','backpack',0]};
                if(kind==='general'){
                    source={path:['inventory','backpack',0],snapshot:copy(initialItems.inventory.backpack[0])};
                    selection={source,application:{kind:'self',actionPoints:0},target_character_id:actorSheet.id,treatment_request_id:null,server_authoritative:true,server_operation:'general'};
                    // Профиль3ОД + штатное доставание из рюкзака2ОД; сервер проверяет минимум заново.
                    startBody={location_character_id:actorId,action_points:5,allow_deferred:true,pending_action_id:id,pending_action_label:ids.itemName,consumable_selection:selection};
                    await request(combatUrl+'/spend',startBody,token);
                }else await act({...startBody,pending_action_id:id});
                state=await getState();actor=state.characters.find(c=>c.location_character_id===actorId);
                let expected=copy(actor.deferred_action);assert.equal(expected.id,id);assert.equal(expected.status,'paying');
                ids.total=actor.pending_action.total_action_points;
                assert.equal(expected.remaining_action_points,ids.total-1);assert.ok(ids.total>1);
                if(kind==='attack')assert.equal(ids.total,startBody.action_points);
                if(kind==='general')assert.equal(ids.total,startBody.action_points);
                check('started',{id,actorId,actorSheetId:actorSheet.id,initial:initialItems,startBody,total:ids.total,summary:expected});
                const snapshot=async()=>({state:await getState(),actor:await getSheet(actorSheet.id),target:await getSheet(targetSheet.id),decoy:await getSheet(decoySheet.id)});
                const negative=async(url,body,status)=>{
                    const before=await snapshot();
                    const response=await fetch(base+url,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+token},body:JSON.stringify(body)});
                    const reply=await response.json();assert.equal(response.status,status,JSON.stringify(reply));
                    assert.deepEqual(await snapshot(),before,'Отказ не изменяет сохранённые листы/ресурсы/операцию');
                    return{status:response.status,error:reply.error};
                };
                if(kind!=='general')check('early-tamper-denied',await negative(combatUrl+'/action',{location_character_id:actorId,action_key:'attack',weapon_index:1,
                    target_character_id:decoySheet.id,resume_pending_action_id:id},409));
                await navigateDocument(page,pageUrl,kind+': переход в сцену');const renderer=await sceneReady();
                let before=await readHud(id);assert.equal(before.id,id);
                await navigateDocument(page,pageUrl,kind+': reload paying',{reload:true,afterEpoch:before.epoch});
                const afterRenderer=await sceneReady(),after=await readHud(id);
                assertReloadRecovery(before,after,expected,actorName);
                assertSummary(summary(await getState(),actorId),expected);
                assert.deepEqual(projectItems((await getSheet(actorSheet.id)).data),initialItems);
                check('real-reload-paying',{before,after,renderer,afterRenderer,summary:expected});await screenshot(kind+'-paying-reloaded');
                await offline(); // Штатное автозавершение не гоняется с проверкой подмены готового запроса.
                for(let turn=1;turn<=8&&expected.status==='paying';turn++){
                    const beforePay=copy(expected),s=await getState();
                    assert.equal(s.current_location_character_id,targetId,'Между частями оплаты ход другого участника');
                    await request(combatUrl+'/end_turn',{location_character_id:targetId},token);
                    state=await getState();const paidActor=state.characters.find(c=>c.location_character_id===actorId);
                    expected=copy(paidActor.deferred_action);assertPayment(beforePay,expected,paidActor.action_points_max);
                    assert.deepEqual(projectItems((await getSheet(actorSheet.id)).data),initialItems,'Оплата не применяет эффект заранее');
                    check('paid-turn',{turn,summary:expected,resources:{ap:paidActor.action_points_current,max:paidActor.action_points_max}});
                    if(expected.status==='paying'){
                        await navigateDocument(page,pageUrl,kind+': переход в сцену');await sceneReady();before=await readHud(id);
                        await navigateDocument(page,pageUrl,kind+': reload после платежа '+turn,{reload:true,afterEpoch:before.epoch});await sceneReady();
                        const restored=await readHud(id);assertReloadRecovery(before,restored,expected,actorName);assertSummary(summary(await getState(),actorId),expected);
                        check('real-reload-next-payment',{before,after:restored,summary:expected});await screenshot(kind+'-partial-'+turn);await offline();
                    }
                }
                assert.equal(expected.status,'ready');assert.equal(expected.remaining_action_points,0);
                const readyState=await getState(),readyActor=readyState.characters.find(c=>c.location_character_id===actorId);
                assert.equal(readyState.current_location_character_id,actorId);const remainingAP=readyActor.action_points_current;
                const readySnapshot=await snapshot();
                assert.equal(readySnapshot.target.data.health.current,initialTarget.health.current);
                assert.equal(readySnapshot.decoy.data.health.current,initialDecoy.health.current);
                const beforeChat=await history();assert.ok(beforeChat.connected&&beforeChat.disconnects===0);
                let completed,completionBody;
                if(kind==='general'){
                    const recovered=await request(combatUrl+'/deferred-consumable/'+id,null,token,'GET');
                    assert.equal(recovered.id,id);assert.deepEqual(recovered.selection,selection);assert.equal(recovered.medicine_roll,null);
                    check('saved-selection-recovered',{id:recovered.id,selection:recovered.selection,medicineRoll:recovered.medicine_roll});
                    completionBody={operation_id:id,deferred_action_id:id,source,application:selection.application,combat_location_id:loc.id,actor_location_character_id:actorId};
                    const forged=copy(completionBody);forged.source.snapshot.id=ids.decoyItemId;
                    check('ready-tamper-denied',await negative('/lobbies/characters/'+actorSheet.id+'/general-consumable',forged,409));
                    completed=await request('/lobbies/characters/'+actorSheet.id+'/general-consumable',completionBody,token);
                }else{
                    completionBody={location_character_id:actorId,resume_pending_action_id:id,action_key:kind==='narrative'||kind==='clear-jam'?'attack':'narrative_action',
                        weapon_index:1,target_character_id:decoySheet.id,fire_mode:'unaimed',target_zone:'left_arm',action_points:0,
                        narrative_action_name:'Подменённое имя',equipment_operation:'unequip',equipment_slot:'armor',item_path:['inventory','backpack',1]};
                    completed=await request(combatUrl+'/action',completionBody,token);
                }
                const done=await snapshot(),finalItems=projectItems(done.actor.data);
                assertEffect(kind,initialItems,finalItems,completed,ids);
                assert.equal(done.state.characters.find(c=>c.location_character_id===actorId).action_points_current,remainingAP,'Завершение не списывает ОД повторно');
                assert.equal(summary(done.state,actorId),null);
                assert.deepEqual(done.decoy,readySnapshot.decoy,'Подставная цель не изменена при завершении');
                if(kind!=='attack')assert.deepEqual(done.target,readySnapshot.target);
                check('completed-original-arguments',{completionBody,response:completed,final:finalItems,remainingAP});
                const replayUrl=kind==='general'?'/lobbies/characters/'+actorSheet.id+'/general-consumable':combatUrl+'/action';
                check('replay-denied',await negative(replayUrl,completionBody,409));
                const afterChat=await history();assert.ok(afterChat.connected&&afterChat.disconnects===0);
                const relevant=entry=>kind==='narrative'?entry.username==='Действие'&&entry.message?.includes(ids.narrativeName):kind==='attack'?entry.username==='Бой'&&entry.message?.includes(actorName):false;
                const beforeCount=beforeChat.history.filter(relevant).length,afterCount=afterChat.history.filter(relevant).length;
                assert.equal(afterCount-beforeCount,['narrative','attack'].includes(kind)?1:0,'Ровно одно предусмотренное сообщение');
                const noFormatter=['clear-jam','equip','general'].includes(kind);
                if(noFormatter)assert.deepEqual(afterChat.history,beforeChat.history,'Для этого пути chat formatter отсутствует');
                check('persisted-chat-once',{beforeCount,afterCount,messages:afterChat.history.filter(relevant),noFormatter});
                await navigateDocument(page,pageUrl,kind+': переход в сцену');await sceneReady();
                await page.until(`![...document.querySelectorAll('.combat-deferred-cancel')].some(n=>n.dataset.action===${JSON.stringify(id)})`,'Завершённый долг не восстановился');
                assert.deepEqual(projectItems((await getSheet(actorSheet.id)).data),finalItems,'Повторный вход не применяет эффект снова');
                await screenshot(kind+'-completed');await offline();
                await request(combatUrl+'/end',{},token);
                outcome.status='READY_FOR_REVIEW';save();
            }catch(error){
                outcome.status=String(error.message).includes('BLOCKED_UI')?'BLOCKED_UI':'BLOCKED';outcome.failure={name:error.name,message:cleanError(error)};save();
                await screenshot(kind+'-failure').catch(()=>{});await offline().catch(()=>{});
            }
        }
        result.status=result.cases.every(c=>c.status==='READY_FOR_REVIEW')?'READY_FOR_REVIEW':'BLOCKED';save();
        if(result.status==='BLOCKED')throw Error('Часть фиксированных сценариев заблокирована; см. durable-reload.json');
    }catch(error){result.status='BLOCKED';result.failure={name:error.name,message:cleanError(error)};save();throw Error(cleanError(error));}
    finally{
        if(context&&protocol)await protocol.call('Target.disposeBrowserContext',{browserContextId:context.browserContextId}).catch(()=>{});
        await observer?.close().catch(()=>{});await protocol?.close().catch(()=>{});
    }
};
Object.assign(module.exports,{documentSnapshot,isTransientNavigationError,documentMatches,waitForDocument,CASES,FIXTURE,loadFixture,summary,assertSummary,hudSnapshot,assertReloadRecovery,assertPayment,projectItems,assertEffect,rifle,baseData});
