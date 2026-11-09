// Обычный игрок: разбор, установка реальных ёмкостей и залп через выбор точки мышью.
const assert=require('node:assert/strict');
module.exports=async({peer,request,recipient,token,api,read})=>{
    await peer.evaluate('window.closeCharacterSheet?.();true');
    const all=(await request(api+'/templates',null,token,'GET')).global;
    const make=(name,id)=>{
        const t=all.find(t=>t.name===name);assert.ok(t,'Шаблон '+name);const attrs=t.attributes||{};
        return {id,name:t.name,templateId:t.id,category:t.category,subcategory:t.subcategory,itemClass:t.item_class,attributes:attrs,
            quantity:1,weight:t.weight,volume:t.volume,uses:attrs.uses_remaining??attrs.uses,maxUses:attrs.uses,
            durability:attrs.max_durability??100,maxDurability:attrs.max_durability??100,installedModules:[],modifications:[]};
    };
    const data=(await read()).data,pistol=data.weapons[0];assert.ok(pistol.modifications.length);
    data.inventory.pockets.push(pistol,{id:'salvage-browser-hammer',name:'Молоток',category:'tool',quantity:1},
        make('Бак для струйного огнемета (Бензин)','flame-browser-fuel'),make('Бак сжатого воздуха','flame-browser-air'));
    data.weapons=[make('Струйный огнемёт','flame-browser-gun')];data.activeWeaponIndex=0;
    delete data.equipment.backpack;delete data.equipment.belt;
    data.skills.physical.strength={base:20,bonus:0};data.skills.physical.shooting={base:20,bonus:0};
    await request('/lobbies/characters/'+recipient.id,{data},token,'PUT');
    const open=async()=>{
        await peer.evaluate(`window.openCharacterSheet(${recipient.id},'equipment').then(()=>true)`);
        await peer.until("document.querySelector('#character-crafting-open')",'Кнопка создания в листе');
        await peer.evaluate("document.querySelector('#character-crafting-open').click();true");
        await peer.until("document.querySelector('[data-crafting-tab=salvage]')",'Открыты вкладки создания');
        await peer.evaluate("document.querySelector('[data-crafting-tab=salvage]').click();true");
        await peer.until("document.querySelector('.crafting-salvage select')?.getClientRects().length",'Разбор виден в отдельной вкладке');
    };
    await open();
    await peer.evaluate(`(async()=>{
        const {Server}=await import('/static/js/api.js'),s=await Server.getCrafting(${recipient.id});
        const candidates=s.inventory.filter(e=>e.salvage_kind),tools=s.inventory.filter(e=>e.salvage_tool_bonus>0);
        document.querySelector('.crafting-salvage').open=true;
        const target=document.querySelector('select[aria-label="Предмет для разбора"]');target.value=candidates.findIndex(e=>e.item.id===${JSON.stringify(pistol.id)});target.dispatchEvent(new Event('change'));
        document.querySelector('select[aria-label="Инструмент для разбора"]').value=tools.findIndex(e=>e.item.id==='salvage-browser-hammer');
        window.__originalConfirm=window.confirm;window.confirm=()=>true;
        [...document.querySelectorAll('.crafting-salvage button')].find(b=>b.textContent==='Начать разбор').click();return true;
    })()`);
    await peer.until(`(async()=>{const {Server}=await import('/static/js/api.js');return (await Server.getCrafting(${recipient.id})).state.current?.kind==='salvage'})()`,'Предмет зарезервирован для разбора');
    await peer.evaluate('window.confirm=window.__originalConfirm;delete window.__originalConfirm;true');
    const job=(await request('/lobbies/characters/'+recipient.id+'/crafting',null,token,'GET')).state.current;
    const ready=job.ready_at/60;
    await request(api+'/time',{game_day:Math.floor(ready/1440),game_time_minutes:ready%1440},token,'PATCH');
    await peer.evaluate("[...document.querySelectorAll('#crafting-modal button')].find(b=>b.textContent==='Обновить').click();true");
    await peer.until("[...document.querySelectorAll('#crafting-modal button')].some(b=>b.textContent==='Завершить · к100'&&!b.disabled)",'Истёк срок разбора');
    await peer.evaluate("[...document.querySelectorAll('#crafting-modal button')].find(b=>b.textContent==='Завершить · к100').click();true");
    await peer.until(`(async()=>{const {Server}=await import('/static/js/api.js');return (await Server.getCrafting(${recipient.id})).state.history.at(-1)?.kind==='salvage'})()`,'Разбор завершён игроком');
    let saved=(await read()).data;
    assert.ok(!saved.inventory.pockets.some(i=>i.id===pistol.id));
    assert.ok(saved.crafting.history.at(-1).materials.some(i=>i.name==='Металлические детали'&&i.quantity>0));
    await peer.until("document.querySelector('[data-crafting-tab=other]')",'Вкладки создания снова видны после разбора');
    await peer.evaluate("document.querySelector('[data-crafting-tab=other]').click();true");
    await peer.until(`document.querySelector('.crafting-flamethrowers select[aria-label="Ёмкость огнемёта"]')?.getClientRects().length`,'Обслуживание огнемёта видно во вкладке другого');
    for(const word of ['Бензин','сжатого воздуха']){
        await peer.evaluate(`(()=>{const select=document.querySelector('.crafting-flamethrowers select');select.value=[...select.options].find(o=>o.textContent.includes(${JSON.stringify(word)})).value;[...document.querySelectorAll('.crafting-flamethrowers button')].find(b=>b.textContent.startsWith('Заменить ёмкость')).click();return true})()`);
        await peer.until(`(async()=>{const {Server}=await import('/static/js/api.js');const w=(await Server.getCrafting(${recipient.id})).flamethrowers[0];return !!w.resources[${word==='Бензин'?0:1}]})()`,'Установлена реальная ёмкость: '+word);
        await peer.until(`document.querySelector('.crafting-flamethrowers p')?.textContent.includes(${JSON.stringify(word)})`,'Окно обновило ёмкость: '+word);
    }
    const snapshot=await request('/lobbies/characters/'+recipient.id+'/crafting',null,token,'GET'),p=snapshot.placement;
    const loc=api+'/locations/'+p.location_id;
    const state=await request(loc+'/combat/start',{location_character_ids:[p.actor_id],initiator_location_character_id:p.actor_id,initiator_first:true},token);
    await peer.until(`window.locationCombatState?.status==='active'&&window.locationCombatState.current_character?.character_id===${recipient.id}`,'Обычный игрок получил свой ход');
    await peer.until(`(()=>{const button=[...document.querySelectorAll('#crafting-modal .crafting-flamethrowers button')].find(button=>button.textContent.trim()==='Короткий залп · 3 ОД');return window.locationCombatState?.status==='active'&&window.locationCombatState.current_character?.character_id===${recipient.id}&&button?.getClientRects().length>0&&!button.disabled})()`,'Кнопка короткого залпа видна и готова после обновления интерфейса');
    await peer.evaluate("[...document.querySelectorAll('.crafting-flamethrowers button')].find(b=>b.textContent==='Короткий залп · 3 ОД').click();true");
    await peer.until("!document.querySelector('#crafting-modal')",'Лист закрыт для выбора точки залпа');
    const actor=state.current_character;
    const screen=await peer.evaluate(`(async()=>{
        const m=await import('/static/js/locationScene.js'),g=m.sceneGraphicsForTest(),model=g.characters.get(${recipient.id}).model;
        const v=model.position.clone();v.x+=${Number(actor.facing_x)||0}*2;v.z+=${Number(actor.facing_y)||1}*2;
        g.controls.target.copy(v);g.camera.position.copy(v).add({x:7,y:12,z:13});g.controls.update();
        g.renderer.render(g.scene,g.camera);v.project(g.camera);const r=g.renderer.domElement.getBoundingClientRect();
        return {x:r.left+(v.x+1)*r.width/2,y:r.top+(1-v.y)*r.height/2};
    })()`);
    for(const type of ['mouseMoved','mousePressed','mouseReleased'])await peer.call('Input.dispatchMouseEvent',{type,x:screen.x,y:screen.y,button:type==='mouseMoved'?'none':'left',clickCount:type==='mouseMoved'?0:1});
    await peer.until(`(async()=>{const {Server}=await import('/static/js/api.js');const s=await Server.getCrafting(${recipient.id});return s.flamethrowers[0].resources[0]?.uses===7&&s.flamethrowers[0].resources[1]?.uses===15})()`,'Залп мышью списал топливо и воздух');
    await peer.until("window.locationCombatState?.area_effects?.some(a=>a.shape==='flame_rectangle'&&Array.isArray(a.cells))",'Область огня пришла настоящим сетевым событием');
    await request(loc+'/combat/end',{},token);
    console.log(JSON.stringify({salvage_ui:true,normal_player:true,weapon_removed:true,real_materials:true,
        flame_mount_ui:true,fuel_and_air:true,mouse_target_selection:true,fire_area_network:true,isolated_pripyat:true}));
};
