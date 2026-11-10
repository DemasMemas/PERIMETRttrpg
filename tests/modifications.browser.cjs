const assert=require('node:assert/strict');
module.exports=async({peer,request,recipient,token,api,read})=>{
    await peer.evaluate('window.closeCharacterSheet?.();true');
    const response=await request(api+'/templates',null,token,'GET');const all=response.global;
    const book=all.find(t=>t.attributes?.book_kind==='equipment'&&t.attributes.book_subject==='Пистолеты');
    const pistol=all.find(t=>t.category==='weapon'&&t.subcategory==='Пистолеты'&&String(t.item_class)==='1');
    const tools=all.find(t=>t.category==='tool'&&/оружейника/i.test(t.name)&&/расшир/i.test(t.name));
    const pda=all.find(t=>t.name==='КПК');assert.ok(book&&pistol&&tools&&pda);
    const make=(t,id)=>({id,name:t.name,templateId:t.id,category:t.category,subcategory:t.subcategory,attributes:t.attributes,
        weight:t.weight,volume:t.volume,quantity:1,uses:t.attributes?.uses,maxUses:t.attributes?.uses});
    const data=(await read()).data;
    data.basic={...(data.basic||{}),background:{name:'Оружейник'}};
    data.weapons=[{...make(pistol,'upgrade-browser-pistol'),model:pistol.name,itemClass:pistol.item_class,accuracy:0,ergonomics:20,
        durability:100,maxDurability:100,fireRate:pistol.attributes.fire_rate,installedModules:[],modifications:[],ammo:0,fixedAmmo:[]}];
    data.inventory.pockets.push(make(book,'upgrade-browser-book'),make(tools,'upgrade-browser-tools'),make(pda,'upgrade-browser-pda'));
    await request('/lobbies/characters/'+recipient.id,{data},token,'PUT');
    await peer.evaluate(`window.openCharacterSheet(${recipient.id},'inventory').then(()=>true)`);
    await peer.until("[...document.querySelectorAll('#pockets-container [data-path]')].some(row=>row.textContent.includes('КПК'))",'КПК виден в инвентаре');
    await peer.evaluate("(()=>{const row=[...document.querySelectorAll('#pockets-container [data-path]')].find(row=>row.textContent.includes('КПК'));row.querySelector('[data-widget-key=use]').click();return true})()");
    await peer.until("document.querySelector('#pda-shell-modal .pda-shell-nav button')",'КПК открыт');
    await peer.evaluate("[...document.querySelectorAll('#pda-shell-modal .pda-shell-nav button')].find(button=>button.textContent==='Файлы').click();true");
    await peer.until("[...document.querySelectorAll('#pda-shell-modal .pda-file-card button')].some(button=>button.textContent==='Записать в КПК')",'Книга доступна для записи');
    await peer.evaluate("[...document.querySelectorAll('#pda-shell-modal button')].find(button=>button.textContent==='Записать в КПК').click();true");
    await peer.until("[...document.querySelectorAll('#pda-shell-modal .pda-file-card button')].some(button=>button.textContent==='Читать · 4 часа')",'Книга записана в КПК');
    await peer.evaluate("[...document.querySelectorAll('#pda-shell-modal button')].find(button=>button.textContent==='Читать · 4 часа').click();true");
    await peer.until("document.querySelector('#pda-shell-modal')?.innerText.includes('Чтение идёт')",'Начато настоящее чтение');
    assert.equal((await read()).data.crafting.current.kind,'book');
    await request(api+'/time',{game_day:1,game_time_minutes:270},token,'PATCH');
    await peer.evaluate("[...document.querySelectorAll('#pda-shell-modal .pda-shell-nav button')].find(button=>button.textContent==='Файлы').click();true");
    await peer.until("document.querySelector('#pda-shell-modal')?.innerText.includes('Прочитано')",'Книга прочитана');
    await peer.evaluate("document.querySelector('#pda-shell-modal [data-pda-close]').click();true");
    await peer.evaluate(`window.openCharacterSheet(${recipient.id},'equipment').then(()=>true)`);
    await peer.evaluate("document.querySelector('#character-crafting-open').click();true");
    await peer.until("document.querySelector('#crafting-modal select[aria-label=\"Предмет для модификации\"]')",'Список снаряжения открыт');
    await peer.evaluate("document.querySelector('[data-crafting-tab=modifications]').click();true");
    await peer.evaluate("(()=>{const pane=document.querySelector('[data-crafting-pane=modifications]');pane.querySelector('[data-modification-id=weapon-c23]').click();const t=pane.querySelector('select[aria-label=\"Инструменты для модификации\"]');t.value=[...t.options].find(o=>/оружейника/i.test(o.textContent)&&/расшир/i.test(o.textContent)).value;t.dispatchEvent(new Event('change'));const c=pane.querySelector('input[type=checkbox]');c.checked=true;c.dispatchEvent(new Event('change'));return true})()");
    await peer.until("[...document.querySelectorAll('#crafting-modal button')].some(b=>b.textContent==='Установить самостоятельно')",'Книга, навык и инструменты разрешили установку');
    assert.ok(await peer.evaluate("document.querySelector('#crafting-modal').innerText.includes('Итоговая СЛ -5')"));
    await peer.evaluate("[...document.querySelectorAll('#crafting-modal button')].find(b=>b.textContent==='Установить самостоятельно').click();true");
    await peer.until("document.querySelector('#crafting-modal')?.innerText.includes('Работа идёт')",'Установка началась');
    let saved=(await read()).data;
    assert.equal(saved.crafting.current.kind,'modification');
    assert.equal(saved.inventory.pockets.find(i=>i.id==='upgrade-browser-tools').uses,tools.attributes.uses-20);
    await request(api+'/time',{game_day:1,game_time_minutes:300},token,'PATCH');
    await peer.evaluate("[...document.querySelectorAll('#crafting-modal button')].find(b=>b.textContent==='Обновить').click();true");
    await peer.until("[...document.querySelectorAll('#crafting-modal button')].some(b=>b.textContent==='Завершить · к100'&&!b.disabled)",'Прошло полчаса установки');
    await peer.evaluate("[...document.querySelectorAll('#crafting-modal button')].find(b=>b.textContent==='Завершить · к100').click();true");
    await peer.until(`(async()=>{const {Server}=await import('/static/js/api.js');const c=await Server.getCharacter(${recipient.id});return c.data.weapons[0].modifications[0]?.ruleId==='weapon-c23'})()`,'Модификация установлена на реальный экземпляр');
    saved=(await read()).data;assert.equal(saved.weapons[0].modifications[0].modifiers.ergonomics,7.5);
    assert.equal(saved.crafting.library[0].book_kind,'equipment');
    await peer.until("document.querySelector('#crafting-modal .block-header button')&&!document.querySelector('#crafting-modal').innerText.includes('Работа идёт')",'Завершённая работа исчезла');
    await peer.evaluate("document.querySelector('#crafting-modal .block-header button').click();true");
    await peer.until("document.querySelector('.installed-rule-modification')?.textContent.includes('Оружейник')",'В листе показано защищённое улучшение');
    const helmet=all.find(t=>t.category==='helmet'&&!t.attributes?.soft_headwear);assert.ok(helmet);
    saved.equipment={...saved.equipment,helmet:{...make(helmet,'upgrade-browser-helmet'),
        attributes:{...helmet.attributes,slots:[{type:'visor',label:'Забрало',maxItems:1}]},
        installedModules:[{id:'upgrade-browser-visor',name:'Забрало',category:'visor',slotType:'visor',
            modifications:[{name:'Старое покрытие',description:'Сохранённый эффект покрытия'}]}]}};
    await request('/lobbies/characters/'+recipient.id,{data:saved},token,'PUT');
    await peer.evaluate(`window.openCharacterSheet(${recipient.id},'equipment').then(()=>true)`);
    await peer.until("document.querySelector('.loadout-slot[data-equipment-open=helmet]')",'Экипировка загружена');
    await peer.evaluate("document.querySelector('.loadout-slot[data-equipment-open=helmet]').click();true");
    await peer.until("document.querySelector('#sheet-tab-equipment button[onclick^=openVisorModificationsModal]')",
        'В экипировке доступен просмотр забрала');
    await peer.evaluate("document.querySelector('#sheet-tab-equipment button[onclick^=openVisorModificationsModal]').click();true");
    const visor=await peer.evaluate(`(()=>{
        const modal=document.querySelector('#visor-modifications-modal');
        return {effect:modal.querySelector('#installed-mods-list').textContent,
            controls:[...modal.querySelectorAll('button,select,input')].map(node=>node.textContent.trim())};
    })()`);
    assert.match(visor.effect,/Старое покрытие/);
    assert.match(visor.effect,/Сохранённый эффект покрытия/);
    assert.deepEqual(visor.controls,['Закрыть'],'Окно забрала позволяет только просматривать улучшения');
    await peer.evaluate("document.querySelector('#visor-modifications-modal #close-modal-btn').click();true");
    console.log(JSON.stringify({equipment_book_read:true,four_game_hours:true,native_weapon_modification:true,normal_player:true,gunsmith_bonus:7.5,tool_uses:20,installation_minutes:30,visor_modifications_read_only:true}));
};
