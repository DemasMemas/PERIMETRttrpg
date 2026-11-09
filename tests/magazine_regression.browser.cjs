// Карточка №53: отдельный сценарий, не запуск полного widgets-пакета.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {assertGpu}=require('./helpers/browser_isolation.cjs');
const MAGAZINE_ID='magazine-repro-53',WEAPON_ID='weapon-repro-53';
const MAGAZINE_NAME='Магазин синхронизации №53';
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function projectMagazine(data,magazineId,weaponId){
    const ammo=magazine=>(magazine?.ammo||[]).map(stack=>({id:stack.id,quantity:Number(stack.quantity),variant:stack.attributes?.ammo_variant||stack.ammo_variant||null}));
    const project=item=>item?{id:item.id,templateId:item.templateId,name:item.name,ammo:ammo(item)}:null;
    const inventory=[];let copies=0;
    const walk=(value,keys=[])=>{
        if(!value||typeof value!=='object')return;
        if(value.id===magazineId){copies++;if(keys[0]!=='weapons')inventory.push({...project(value),path:keys.join('.')});}
        for(const [key,child]of Object.entries(value))walk(child,[...keys,key]);
    };
    walk(data);
    const weapon=(data.weapons||[]).find(item=>item.id===weaponId);
    return{revision:Number(data._revision),inventory,installed:project(weapon?.installedMagazine),weaponAmmo:Number(weapon?.ammo||0),copies};
}
function assertBefore(snapshot,expected){
    assert.equal(snapshot.copies,1,'До установки ровно один экземпляр');
    assert.equal(snapshot.installed,null);assert.equal(snapshot.weaponAmmo,0);
    assert.equal(snapshot.inventory.length,1);assert.equal(snapshot.inventory[0].id,expected.id);
    assert.equal(snapshot.inventory[0].path,'inventory.backpack.0');
    assert.equal(snapshot.inventory[0].ammo.reduce((sum,stack)=>sum+stack.quantity,0),expected.count);
}
function assertInstalled(snapshot,expected){
    assert.equal(snapshot.copies,1,'Магазин не потерян и не продублирован');
    assert.equal(snapshot.inventory.length,0,'В инвентаре магазина больше нет');
    assert.equal(snapshot.installed?.id,expected.id);assert.equal(snapshot.installed?.name,expected.name);
    assert.equal(snapshot.installed?.templateId,expected.templateId);
    assert.deepEqual(snapshot.installed?.ammo,expected.ammo,'Состав, ID и порядок патронов сохранены');
    assert.equal(snapshot.weaponAmmo,expected.count);
}
function sheetSnapshot(magazineId,magazineName){
    const visible=node=>Boolean(node&&node.getClientRects().length&&getComputedStyle(node).display!=='none'&&getComputedStyle(node).visibility!=='hidden');
    const inventory=document.querySelector('#sheet-tab-inventory'),weapon=document.querySelector('#weapons-container [data-weapon-index="0"]');
    const rows=[...(inventory?.querySelectorAll('[data-widget-identity]')||[])].filter(node=>JSON.parse(node.dataset.widgetIdentity).id===magazineId);
    const installed=[...(weapon?.querySelectorAll('span')||[])].filter(node=>node.textContent.startsWith(magazineName+' ('));
    return{sheetVisible:visible(document.querySelector('#character-sheet-modal')),inventoryVisible:visible(inventory),
        inventoryRows:rows.map(node=>({id:JSON.parse(node.dataset.widgetIdentity).id,text:node.innerText,visible:visible(node)})),
        weaponVisible:visible(weapon),installed:installed.map(node=>({text:node.textContent,visible:visible(node)})),
        installButtons:weapon?.querySelectorAll('[onclick="equipMagazineToWeapon(0)"]').length||0,
        removeButtons:weapon?.querySelectorAll('[onclick="unequipMagazineFromWeapon(0)"]').length||0};
}
function sheetExpression(){return`(${sheetSnapshot.toString()})(${JSON.stringify(MAGAZINE_ID)},${JSON.stringify(MAGAZINE_NAME)})`;}
function eventCollector(characterId){
    return`(()=>{window.magazineEvents=[];socket.on('character_data_updated',message=>{if(message.character_id===${characterId}&&message.updates?.data){const snapshot=(${projectMagazine.toString()})(message.updates.data,${JSON.stringify(MAGAZINE_ID)},${JSON.stringify(WEAPON_ID)});magazineEvents.push({source:message.source||null,...snapshot});if(magazineEvents.length>20)magazineEvents.shift();}});return true})()`;
}
function readyMenu(magazineName,count){
    const modal=document.querySelector('#magazine-select-modal'),select=modal?.querySelector('#magazine-select');
    const options=[...(select?.options||[])],confirm=modal?.querySelector('#confirm-magazine-btn');
    return Boolean(confirm&&confirm.getClientRects().length&&modal&&modal.getClientRects().length&&options.length===1&&select.value==='0'
        &&options[0].textContent===magazineName+' ('+count+' патр.)'&&!confirm.disabled);
}
async function installViaUI(options){
    await options.click('#sheet-tabs [data-tab="equipment"]');
    await options.click('#sheet-content .loadout-slot[data-equipment-open="weapon-0"]');
    await options.click('#weapons-container [data-weapon-index="0"] [onclick="equipMagazineToWeapon(0)"]');
    await options.waitMenu();await options.recordMenu();
    await options.click('#confirm-magazine-btn');
}
async function waitInstalled(read,expected,wait=pause){
    let last;
    for(let i=0;i<120;i++){
        last=await read();
        try{assertInstalled(last,expected);return last;}catch{await wait(250);}
    }
    assertInstalled(last,expected);
}
module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession})=>{
    assert.ok(safeSession&&process.env.VTT_SAFE_DB_MODE==='owned-file'&&process.env.VTT_SAFE_SCENARIO==='magazine');
    const result={cardId:'6ae489193ea415a0a48a6a81',cardUrl:'https://trello.com/c/9AcNX2J2',status:'RUNNING',
        runId:safeSession.runId,port:Number(new URL(base).port),phases:[]};
    const save=()=>fs.writeFileSync(path.join(safeSession.artifacts,'magazine-regression.json'),JSON.stringify(result,null,2));
    const record=(stage,data={})=>{result.phases.push({stage,...data});save();};save();
    let protocol,peer,context;
    const screenshot=async(client,label)=>fs.writeFileSync(path.join(safeSession.artifacts,label+'.png'),Buffer.from((await client.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    const click=async(client,selector)=>{
        await client.until(`(()=>{const n=document.querySelector(${JSON.stringify(selector)});return n&&!n.disabled&&n.getClientRects().length})()`,'Кнопка доступна');
        await client.call('Page.bringToFront');
        const point=await client.evaluate(`(async()=>{const n=document.querySelector(${JSON.stringify(selector)});n.scrollIntoView({behavior:'instant',block:'center'});await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));const r=n.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;if(!n.contains(document.elementFromPoint(x,y)))throw Error('Кнопка перекрыта');return{x,y}})()`);
        for(const type of ['mousePressed','mouseReleased'])await client.call('Input.dispatchMouseEvent',{type,...point,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1});
    };
    try{
        const makePlayer=async name=>{
            await request('/auth/register',{username:name,email:name+'@example.test',password:'synthetic-magazine-password'});
            const login=await request('/auth/login',{username:name,password:'synthetic-magazine-password'});
            await request(`/lobbies/${lobby.id}/join`,{},login.access_token);return login;
        };
        const owner=await makePlayer('magazine-owner'),observer=await makePlayer('magazine-observer');
        assert.notEqual(owner.user_id,observer.user_id);
        const weaponTemplate=await request(`/lobbies/${lobby.id}/templates`,{name:'Пистолет синхронизации №53',category:'weapon',subcategory:'Пистолеты',weight:1,volume:1,
            attributes:{caliber:'9x19',fixedMagazine:false,damage:25,range:20}},token);
        const weaponTemplateId=weaponTemplate.id+1000000; // Существующее отображение локальных шаблонов UI.
        const attributes={caliber:'9x19',capacity:15,compatible_weapons:[weaponTemplateId],emptyWeight:.1};
        const magazineTemplate=await request(`/lobbies/${lobby.id}/templates`,{name:MAGAZINE_NAME,category:'magazine',weight:.1,volume:.2,attributes},token);
        const magazineTemplateId=magazineTemplate.id+1000000;
        const ammo=[{id:'mag-round-a',name:'Патроны 9x19 А',category:'ammo',quantity:3,attributes:{caliber:'9x19',ammo_variant:'bp',damage:25}},
            {id:'mag-round-b',name:'Патроны 9x19 Б',category:'ammo',quantity:4,attributes:{caliber:'9x19',ammo_variant:'ep',damage:25}}];
        const character=await request(`/lobbies/${lobby.id}/characters`,{name:'Владелец магазина №53',data:{
            health:{current:700,max:700,effects:[]},skills:{physical:{strength:{base:10,bonus:0}}},
            equipment:{backpack:{id:'magazine-bag',name:'Рюкзак',attributes:{capacity:20}}},
            weapons:[{id:WEAPON_ID,name:'Пистолет синхронизации №53',templateId:weaponTemplateId,category:'weapon',type:'firearm',ammo:0,installedMagazine:null,durability:100,attributes:{caliber:'9x19',fixedMagazine:false}}],
            inventory:{pockets:[],backpack:[{id:MAGAZINE_ID,name:MAGAZINE_NAME,templateId:magazineTemplateId,category:'magazine',quantity:1,volume:.2,attributes,ammo}]},
        }},owner.access_token);
        assert.equal(character.owner_id,owner.user_id);
        const characterUrl=`/lobbies/characters/${character.id}`;
        const denied=await fetch(base+characterUrl,{headers:{Authorization:'Bearer '+observer.access_token}});
        assert.equal(denied.status,403,'Наблюдателю ещё не выдан доступ');await denied.arrayBuffer();
        await request(characterUrl,{visible_to:[observer.user_id],editable_to:[]},token,'PUT');
        const ownerState=await request(characterUrl,null,owner.access_token,'GET');
        const observerState=await request(characterUrl,null,observer.access_token,'GET');
        assert.equal(ownerState.can_edit,true);assert.equal(observerState.can_edit,false);
        assert.ok(observerState.visible_to.includes(observer.user_id));assert.deepEqual(observerState.editable_to,[]);
        let before=projectMagazine(ownerState.data,MAGAZINE_ID,WEAPON_ID);
        const expected={id:MAGAZINE_ID,name:MAGAZINE_NAME,templateId:magazineTemplateId,count:7,ammo:before.inventory[0]?.ammo};
        assertBefore(before,expected);assert.deepEqual(projectMagazine(observerState.data,MAGAZINE_ID,WEAPON_ID),before);
        record('access-and-initial-state',{characterId:character.id,ownerId:owner.user_id,observerId:observer.user_id,
            deniedBeforeGrant:denied.status,visibleTo:observerState.visible_to,editableTo:observerState.editable_to,before});
        const version=await(await fetch(`http://127.0.0.1:${debugPort}/json/version`)).json();
        protocol=await connect(version.webSocketDebuggerUrl);context=await protocol.call('Target.createBrowserContext');
        const target=await protocol.call('Target.createTarget',{url:base,browserContextId:context.browserContextId});
        let targetInfo;
        for(let i=0;i<100&&!targetInfo;i++){targetInfo=(await(await fetch(`http://127.0.0.1:${debugPort}/json`)).json()).find(item=>item.id===target.targetId);if(!targetInfo)await pause(100);}
        assert.ok(targetInfo,'Создан собственный target наблюдателя');peer=await connect(targetInfo.webSocketDebuggerUrl);
        await peer.call('Page.enable');await peer.call('Runtime.enable');
        const clients=[{client:page,login:owner,label:'owner'},{client:peer,login:observer,label:'observer'}];
        const ready=async({client,login,label},authenticate)=>{
            if(authenticate){
                await client.call('Page.navigate',{url:base});await client.until('location.origin==='+JSON.stringify(base),'Собственный origin');
                await client.evaluate(`localStorage.setItem('access_token',${JSON.stringify(login.access_token)});localStorage.setItem('user_id',${JSON.stringify(String(login.user_id))})`);
                await client.call('Page.navigate',{url:base+`/lobbies/${lobby.id}/page`});
            }
            await client.until(`typeof openCharacterSheet==='function'&&window.isGM===false&&Number(localStorage.getItem('user_id'))===${login.user_id}&&window.socket?.connected`,'Авторизованный обычный игрок');
            await client.evaluate(`(async()=>{window.magScene=await import('/static/js/lobby3d.js')})()`);
            await client.until(`magScene.chunksMap.size===2&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Загружена обычная игровая сцена');
            const renderer=await client.evaluate(`(()=>{const gl=magScene.renderer.getContext(),ext=gl.getExtension('WEBGL_debug_renderer_info');return ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):null})()`);assertGpu(renderer);
            await client.evaluate(eventCollector(character.id));
            await client.evaluate(`(async()=>{await openCharacterSheet(${character.id})})()`);
            await client.until(`document.body.classList.contains('character-sheet-open')&&Boolean(document.querySelector('#weapons-container [data-weapon-index="0"]'))`,'Лист и карточка оружия готовы');
            const ack=await client.evaluate(`new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Нет подтверждения подписки')),10000);socket.emit('join_character',{character_id:${character.id},token:localStorage.getItem('access_token')},ack=>{clearTimeout(timer);resolve({ok:ack?.ok===true,characterId:ack?.character_id})})})`);
            assert.equal(ack.ok,true);assert.equal(ack.characterId,character.id);
            record(label+':ready',{renderer,subscription:ack});
        };
        const snapshot=client=>client.evaluate(sheetExpression());
        const showInventory=async(entry,installed)=>{
            await click(entry.client,'#sheet-tabs [data-tab="equipment"]');
            await click(entry.client,'.equipment-panel-tab[data-panel="inventory"]');
            await entry.client.until(`(()=>{const s=${sheetExpression()};return s.sheetVisible&&s.inventoryVisible&&s.inventoryRows.length===${installed?0:1}${installed?'':'&&s.inventoryRows[0].visible'}})()`,'Инвентарь показывает правильное число магазинов');
            const ui=await snapshot(entry.client);assert.equal(ui.inventoryRows.length,installed?0:1);
            if(!installed)assert.equal(ui.inventoryRows[0].id,MAGAZINE_ID);
            record(entry.label+(installed?':inventory-after':':inventory-before'),{ui});
        };
        const showInstalled=async entry=>{
            await click(entry.client,'#sheet-tabs [data-tab="equipment"]');
            await click(entry.client,'#sheet-content .loadout-slot[data-equipment-open="weapon-0"]');
            await entry.client.until(`(()=>{const s=${sheetExpression()};return s.weaponVisible&&s.installed.length===1&&s.installed[0].visible&&s.installed[0].text.includes('(7/15)')&&s.installButtons===0&&s.removeButtons===1})()`,'Видимый установленный магазин и 7/15');
            const viewport=await entry.client.evaluate(`(async()=>{
                const weapon=document.querySelector('#weapons-container [data-weapon-index="0"]');
                const node=[...weapon.querySelectorAll('span')].find(n=>n.textContent.startsWith(${JSON.stringify(MAGAZINE_NAME)}+' ('));
                node.scrollIntoView({block:'center',inline:'nearest'});
                await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
                const r=node.getBoundingClientRect();let bounds={left:0,top:0,right:innerWidth,bottom:innerHeight};
                for(let p=node.parentElement;p;p=p.parentElement){const style=getComputedStyle(p),b=p.getBoundingClientRect();
                    if(/auto|scroll|hidden|clip/.test(style.overflowX)){bounds.left=Math.max(bounds.left,b.left);bounds.right=Math.min(bounds.right,b.right);}
                    if(/auto|scroll|hidden|clip/.test(style.overflowY)){bounds.top=Math.max(bounds.top,b.top);bounds.bottom=Math.min(bounds.bottom,b.bottom);}}
                return{text:node.textContent,rect:{left:r.left,top:r.top,right:r.right,bottom:r.bottom},bounds,
                    fullyVisible:r.width>0&&r.height>0&&r.left>=bounds.left-1&&r.right<=bounds.right+1&&r.top>=bounds.top-1&&r.bottom<=bounds.bottom+1};
            })()`);
            record(entry.label+':installed-ui',{ui:await snapshot(entry.client),viewport});
            assert.equal(viewport.fullyVisible,true,'Полная строка магазина находится внутри viewport и clipping-предков');
        };
        for(const entry of clients){await ready(entry,true);await showInventory(entry,false);}
        before=projectMagazine((await request(characterUrl,null,owner.access_token,'GET')).data,MAGAZINE_ID,WEAPON_ID);
        assertBefore(before,expected);assert.deepEqual(before.inventory[0].ammo,expected.ammo);
        record('before-ui-action',{snapshot:before});
        for(const entry of clients)await entry.client.evaluate('magazineEvents.length=0;true');
        await screenshot(peer,'magazine-observer-before');
        await installViaUI({click:selector=>click(page,selector),
            waitMenu:()=>page.until(`(${readyMenu.toString()})(${JSON.stringify(MAGAZINE_NAME)},7)`,'Выбран исходный магазин, подтверждение доступно'),
            recordMenu:async()=>record('owner:menu-ready',{selected:await page.evaluate(`document.querySelector('#magazine-select').selectedOptions[0].textContent`)}),
        });
        const after=await waitInstalled(async()=>projectMagazine((await request(characterUrl,null,owner.access_token,'GET')).data,MAGAZINE_ID,WEAPON_ID),expected);
        assert.ok(after.revision>before.revision,'Сервер зафиксировал изменение');
        await page.evaluate(`(async()=>{const {Server}=await import('/static/js/api.js');await Server.waitForCharacterSave(${character.id})})()`);
        const observerAfter=projectMagazine((await request(characterUrl,null,observer.access_token,'GET')).data,MAGAZINE_ID,WEAPON_ID);
        assertInstalled(observerAfter,expected);record('server-after',{owner:after,observer:observerAfter});
        for(const entry of clients){
            await entry.client.until(`magazineEvents.some(e=>e.revision>${before.revision}&&e.installed?.id===${JSON.stringify(MAGAZINE_ID)}&&e.inventory.length===0)`,'Получено реальное character_data_updated');
            const events=await entry.client.evaluate('magazineEvents');
            const event=events.find(event=>event.revision>before.revision&&event.installed?.id===MAGAZINE_ID&&event.inventory.length===0);
            assertInstalled(event,expected);assert.equal(event.source,'reload');record(entry.label+':event',{event});
            await showInventory(entry,true);await showInstalled(entry);await screenshot(entry.client,'magazine-'+entry.label+'-installed');
        }
        for(const entry of clients){
            await entry.client.call('Page.reload',{ignoreCache:true});await ready(entry,false);
            const persisted=projectMagazine((await request(characterUrl,null,entry.login.access_token,'GET')).data,MAGAZINE_ID,WEAPON_ID);
            assertInstalled(persisted,expected);record(entry.label+':reload-server',{snapshot:persisted});
            await showInventory(entry,true);await showInstalled(entry);await screenshot(entry.client,'magazine-'+entry.label+'-reload');
        }
        result.status='READY_FOR_REVIEW';save();
    }catch(error){
        result.status='BLOCKED';result.failure={name:error.name,message:String(error.message).replace(/eyJ[\w-]+\.[\w-]+\.[\w-]+/g,'[test token]')};save();
        await screenshot(page,'magazine-failure-owner').catch(()=>{});if(peer)await screenshot(peer,'magazine-failure-observer').catch(()=>{});throw error;
    }finally{
        if(context&&protocol)await protocol.call('Target.disposeBrowserContext',{browserContextId:context.browserContextId}).catch(()=>{});
        await peer?.close().catch(()=>{});await protocol?.close().catch(()=>{});
    }
};
Object.assign(module.exports,{projectMagazine,assertBefore,assertInstalled,sheetSnapshot,sheetExpression,eventCollector,readyMenu,installViaUI,waitInstalled,MAGAZINE_ID,WEAPON_ID,MAGAZINE_NAME});
