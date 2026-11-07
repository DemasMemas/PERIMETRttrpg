// Обмен в настоящей Припяти; изменения относятся только к БД данного запуска.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {loadFixture,waitForDocument}=require('./durable_reload.browser.cjs');
module.exports=async({page,request,token,lobby,base,connect,debugPort,safeSession})=>{
    assert.equal(process.env.VTT_SAFE_SCENARIO,'trade');assert.ok(safeSession.verified);
    const api=`/lobbies/${lobby.id}`,{fixture}=loadFixture();
    const location=await request(api+'/locations',{...fixture,objects:undefined,world_tile_x:7,world_tile_z:8},token),loc=api+'/locations/'+location.id;
    await request(loc+'/objects/batch',{objects:fixture.objects},token);
    const make=async(name,money,items,x)=>{
        const character=await request(api+'/characters',{name,data:{inventory:{money,pockets:items,backpack:[]},health:{current:700,max:700,effects:[]}}},token);
        const placed=await request(loc+'/spawn_character',{character_id:character.id,tile_x:x,tile_y:48},token);
        return {character,placed};
    };
    const actor=await make('Отправитель',1000,[{id:'trade-bandage',name:'Бинт',category:'consumable',quantity:5,volume:.1,weight:.1}],41);
    const target=await make('Получатель',500,[{id:'trade-water',name:'Вода',category:'consumable',quantity:3,volume:2,weight:1}],42);
    const href=base+api+'/page?location='+location.id,epoch=await page.evaluate('performance.timeOrigin');
    await page.call('Page.navigate',{url:href});await waitForDocument(page,{href,afterEpoch:epoch,label:'Припять: обмен'});
    await page.until(`window.socket?.connected&&window.currentLocationId===${location.id}&&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,'Сцена готова');
    const actorId=actor.placed.location_character_id;
    assert.ok(actorId,JSON.stringify(actor.placed).slice(0,400));
    await page.evaluate(`import('/static/js/locationScene.js').then(m=>m.showCharacterTradeMenu(${actorId},${actor.character.id},${target.character.id})).then(()=>true)`);
    await page.until(`document.querySelector('.character-trade .trade-item-row')`,'Окно обмена с реальными инвентарями');
    assert.ok(await page.evaluate(`(()=>{const row=document.querySelector('.trade-item-row');return row.scrollWidth<=row.clientWidth&&row.querySelector('label').getBoundingClientRect().width>150})()`),'Название предмета имеет место, строка не вылезает по горизонтали');
    assert.equal(await page.evaluate(`document.querySelector('.trade-confirm').disabled`),true);
    await page.evaluate(`(()=>{const side=document.querySelector('[data-trade-side="actor"]');const input=side.querySelector('.trade-money');input.value='250';input.dispatchEvent(new Event('input'));return true})()`);
    assert.match(await page.evaluate(`document.querySelector('[data-trade-side="actor"] .trade-balance').textContent`),/750/);
    await page.evaluate(`(()=>{const row=document.querySelector('[data-trade-side="target"] .trade-item-row');row.querySelector('[type=checkbox]').click();const n=row.querySelector('.trade-quantity');n.value='2';n.dispatchEvent(new Event('input'));return true})()`);
    await page.evaluate(`(()=>{const s=document.querySelector('[data-trade-side="target"] .trade-search');s.value='Ничего';s.dispatchEvent(new Event('input'));return true})()`);
    assert.match(await page.evaluate(`document.querySelector('.trade-summary').textContent`),/Получаете: 2/);
    await page.evaluate(`(()=>{const s=document.querySelector('[data-trade-side="target"] .trade-search');s.value='';s.dispatchEvent(new Event('input'));return true})()`);
    const shot=await page.call('Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(safeSession.artifacts,'trade-desktop.png'),Buffer.from(shot.data,'base64'));
    await page.call('Emulation.setDeviceMetricsOverride',{width:480,height:900,deviceScaleFactor:1,mobile:false});
    assert.ok(await page.evaluate(`document.querySelector('.character-trade').getBoundingClientRect().width<=480`));
    assert.ok(await page.evaluate(`(()=>{const row=document.querySelector('.trade-item-row');return row.scrollWidth<=row.clientWidth&&row.querySelector('label').getBoundingClientRect().width>150})()`),'Узкое окно сохраняет читаемые названия');
    const narrow=await page.call('Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(safeSession.artifacts,'trade-narrow.png'),Buffer.from(narrow.data,'base64'));
    await page.evaluate(`document.querySelector('.trade-confirm').click();true`);
    await page.until(`!document.querySelector('.character-trade')||document.querySelector('.character-trade').parentElement.style.display==='none'`,'Обмен завершён');
    const read=id=>request('/lobbies/characters/'+id,null,token,'GET');
    let a=await read(actor.character.id),b=await read(target.character.id);
    assert.equal(a.data.inventory.money,750);assert.equal(b.data.inventory.money,750);
    assert.equal(a.data.inventory.backpack.find(item=>item.id==='trade-water').quantity,2);
    assert.equal(b.data.inventory.pockets.find(item=>item.id==='trade-water').quantity,1);
    await page.call('Emulation.clearDeviceMetricsOverride');
    await page.evaluate(`import('/static/js/locationScene.js').then(m=>m.showCharacterTradeMenu(${actorId},${actor.character.id},${target.character.id})).then(()=>true)`);
    await page.evaluate(`(()=>{const input=document.querySelector('[data-trade-side="actor"] .trade-money');input.value='750';input.dispatchEvent(new Event('input'));document.querySelector('.trade-confirm').click();return true})()`);
    await page.until(`document.querySelector('.character-trade').parentElement.style.display==='none'`,'Передача только рублей завершена');
    a=await read(actor.character.id);b=await read(target.character.id);
    assert.equal(a.data.inventory.money,0);assert.equal(b.data.inventory.money,1500);
    const catalog=await request(api+'/templates?category=consumable',null,token,'GET');
    const water=catalog.global.find(template=>template.name==='Вода'),bandage=catalog.global.find(template=>template.name==='Бинт');
    assert.ok(water&&bandage,'Настоящие товары полного каталога загружены в собственную БД');
    a.data.inventory.money=1000;a.data.inventory.pockets[0].templateId=bandage.id;
    b.data.inventory.money=500;b.data.is_npc=true;b.data.inventory.pockets[0].templateId=water.id;b.data.inventory.pockets[0].quantity=3;
    await request('/lobbies/characters/'+a.id,{data:a.data},token,'PUT');
    await request('/lobbies/characters/'+b.id,{data:b.data},token,'PUT');
    await page.evaluate(`import('/static/js/locationScene.js').then(m=>m.showCharacterTradeMenu(${actorId},${a.id},${b.id})).then(()=>true)`);
    await page.evaluate(`document.querySelector('.trade-configure').click();true`);
    await page.until(`document.querySelector('.merchant-price-list .merchant-price-row')`,'Прайс НПС открыт');
    assert.ok(await page.evaluate(`(()=>{const row=document.querySelector('.merchant-price-row');return row.scrollWidth<=row.clientWidth})()`));
    assert.equal(await page.evaluate(`document.querySelectorAll('.merchant-price-row').length`),100,'Большой каталог выводится порциями');
    const setPrice=async(id,sell,buy)=>page.evaluate(`(()=>{const search=document.querySelector('.merchant-search');search.value=${JSON.stringify(catalog.global.find(template=>template.id===id).name)};search.dispatchEvent(new Event('input'));const row=document.querySelector('[data-template-id="${id}"]');for(const [side,value] of Object.entries(${JSON.stringify({sell,buy})})){const input=row.querySelector('[data-side="'+side+'"]');input.value=value;input.dispatchEvent(new Event('input'));}return true})()`);
    await setPrice(water.id,75,40);await setPrice(bandage.id,'',25);
    await page.evaluate(`document.querySelector('.merchant-enabled').click();true`);
    await page.until(`!document.querySelector('.notification')`,'Предыдущие уведомления завершились');
    const merchantShot=await page.call('Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(safeSession.artifacts,'merchant-prices.png'),Buffer.from(merchantShot.data,'base64'));
    await page.call('Emulation.setDeviceMetricsOverride',{width:480,height:900,deviceScaleFactor:1,mobile:false});
    assert.ok(await page.evaluate(`(()=>{const row=document.querySelector('.merchant-price-row');return row.scrollWidth<=row.clientWidth})()`),'Прайс помещается в узкое окно');
    assert.ok(await page.evaluate(`(()=>{const box=document.querySelector('.merchant-save').getBoundingClientRect();return box.top>=0&&box.bottom<=innerHeight})()`),'Сохранение прайса доступно без прокрутки всего окна');
    assert.ok(await page.evaluate(`(()=>{const box=document.querySelector('.merchant-save').getBoundingClientRect();const modal=document.querySelector('.merchant-editor').getBoundingClientRect();return box.bottom<=modal.bottom-8})()`),'Кнопка сохранения целиком видна внутри прайса');
    const merchantNarrow=await page.call('Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(safeSession.artifacts,'merchant-prices-narrow.png'),Buffer.from(merchantNarrow.data,'base64'));
    await page.call('Emulation.clearDeviceMetricsOverride');
    await page.evaluate(`document.querySelector('.merchant-save').click();true`);
    await page.until(`document.querySelector('.character-trade h3')?.textContent==='Торговля'`,'Прайс сохранён, торговля открыта');
    assert.equal(await page.evaluate(`document.querySelector('.trade-money').readOnly`),true);
    await page.evaluate(`(()=>{const a=document.querySelector('[data-trade-side="actor"] .trade-item-row');a.querySelector('[type=checkbox]').click();const b=document.querySelector('[data-trade-side="target"] .trade-item-row');b.querySelector('[type=checkbox]').click();const n=b.querySelector('.trade-quantity');n.value='2';n.dispatchEvent(new Event('input'));return true})()`);
    assert.equal(await page.evaluate(`Number(document.querySelector('[data-trade-side="actor"] .trade-money').value)`),125);
    await page.until(`!document.querySelector('.notification')`,'Уведомление сохранения завершилось');
    const shopShot=await page.call('Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(safeSession.artifacts,'merchant-purchase.png'),Buffer.from(shopShot.data,'base64'));
    await page.evaluate(`document.querySelector('.trade-confirm').click();true`);
    await page.until(`document.querySelector('.character-trade').parentElement.style.display==='none'`,'Автоматическая торговля завершена');
    a=await read(actor.character.id);b=await read(target.character.id);
    assert.equal(a.data.inventory.money,875);assert.equal(b.data.inventory.money,625);
    assert.equal(a.data.inventory.pockets.find(item=>item.id==='trade-bandage').quantity,4);
    assert.equal(b.data.inventory.pockets.find(item=>item.id==='trade-water').quantity,1);
    assert.equal(new Set(a.data.inventory.backpack.map(item=>item.id)).size,a.data.inventory.backpack.length,'Повторная передача не дублирует ID стопок');
    assert.match(a.data.health.combatMeta.actionJournal.at(-1).label,/Торговля/);
    await page.evaluate(`import('/static/js/characters.js').then(m=>m.loadLobbyCharacters()).then(()=>true)`);
    await page.until(`document.querySelector('[data-character-id="${b.id}"] .npc-loot-open')`,'У НПС доступна кнопка пулов добычи');
    await page.evaluate(`document.querySelector('[data-character-id="${b.id}"] .npc-merchant-open').click();true`);
    await page.until(`document.querySelector('.merchant-editor .merchant-money')`,'Настройка торговли доступна напрямую из списка НПС');
    assert.equal(await page.evaluate(`Number(document.querySelector('.merchant-money').value)`),625);
    await page.evaluate(`document.querySelector('.merchant-editor .trade-close').click();true`);
    await page.evaluate(`document.querySelector('[data-character-id="${b.id}"] .npc-loot-open').click();true`);
    await page.until(`document.querySelector('.npc-loot-editor')`,'Редактор добычи открыт из списка НПС');
    await page.evaluate(`(()=>{for(const [key,value] of Object.entries({name:'Проверка бинтов',budget:80,money_min:7,money_max:7})){const input=document.querySelector('[data-profile="'+key+'"]');input.value=value;input.dispatchEvent(new Event('input'));}for(const [key,value] of Object.entries({subcategory:'Кровь',item_class:'1',min_price:40,max_price:40,draws:2,quantity:1})){const input=document.querySelector('[data-pool="'+key+'"]');input.value=value;input.dispatchEvent(new Event(input.tagName==='SELECT'?'change':'input'));}return true})()`);
    assert.match(await page.evaluate(`document.querySelector('.loot-match-count').textContent`),/1$/);
    assert.equal(await page.evaluate(`document.querySelector('.loot-generate').disabled`),true);
    await page.evaluate(`document.querySelector('.loot-save').click();true`);
    await page.until(`!document.querySelector('.loot-generate').disabled`,'Профиль сохранён без наполнения инвентаря');
    let beforeLoot=await read(b.id);
    assert.equal(beforeLoot.data.inventory.money,625);
    await page.call('Emulation.setDeviceMetricsOverride',{width:480,height:900,deviceScaleFactor:1,mobile:false});
    assert.ok(await page.evaluate(`(()=>{const box=document.querySelector('.npc-loot-editor');return box.scrollWidth<=box.clientWidth&&box.getBoundingClientRect().width<=480})()`),'Редактор пулов не вылезает за узкий экран');
    const lootShot=await page.call('Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(safeSession.artifacts,'npc-loot-narrow.png'),Buffer.from(lootShot.data,'base64'));
    await page.call('Emulation.clearDeviceMetricsOverride');
    await page.evaluate(`document.querySelector('.loot-generate').click();true`);
    await page.until(`document.querySelector('.loot-receipt').textContent.includes('Бинт')`,'Добыча действительно добавлена');
    const withLoot=await read(b.id);
    assert.equal(withLoot.data.inventory.money,632);
    assert.equal(withLoot.data.inventory.backpack.length,beforeLoot.data.inventory.backpack.length+2);
    const receipt=withLoot.data.npcLoot.receipts.at(-1);
    assert.equal(receipt.spent,80);
    await request(api+'/characters/'+b.id+'/loot-pool',{character_revision:beforeLoot.data._revision,operation_id:receipt.operation_id},token);
    assert.equal((await read(b.id)).data.inventory.money,632,'Повтор запроса не добавляет деньги снова');
    await page.evaluate(`document.querySelector('.npc-loot-editor .trade-close').click();true`);
    const vestCatalog=await request(api+'/templates?category=vest',null,token,'GET');
    const readyVests=vestCatalog.global.filter(template=>template.attributes?.pouches?.length);
    assert.ok(readyVests.length>=2,'Каталог содержит готовые разгрузки');
    const pouchCatalog=await request(api+'/templates?category=pouch',null,token,'GET');
    const medical=pouchCatalog.global.find(template=>template.subcategory==='Медицинский'&&template.attributes.capacity===4);
    const excessive=pouchCatalog.global.find(template=>template.volume>8&&template.subcategory!=='Медицинский');
    assert.ok(medical&&excessive);
    const vestBefore=await read(b.id),vestTemplate=readyVests[0],nextVest=readyVests[1];
    vestBefore.data.inventory.backpack.push(
        {id:'medical-pouch-owned',templateId:medical.id,type:medical.id,name:medical.name,category:'pouch',subcategory:medical.subcategory,quantity:1,volume:medical.volume,weight:medical.weight,attributes:medical.attributes,capacity:medical.attributes.capacity,contents:[],isContainer:true},
        {id:'oversize-pouch-owned',templateId:excessive.id,type:excessive.id,name:excessive.name,category:'pouch',subcategory:excessive.subcategory,quantity:1,volume:excessive.volume,weight:excessive.weight,attributes:excessive.attributes,capacity:excessive.attributes.capacity,contents:[],isContainer:true}
    );
    const pouches=JSON.parse(JSON.stringify(vestTemplate.attributes.pouches));
    pouches[0].contents=[{id:'vest-preserved-bandage',templateId:bandage.id,name:'Бинт',category:'consumable',quantity:4,volume:.1,weight:.1}];
    vestBefore.data.equipment||={};
    vestBefore.data.equipment.vest={id:'own-vest',templateId:vestTemplate.id,model:String(vestTemplate.id),name:vestTemplate.name,
        pouches,attributes:vestTemplate.attributes,totalCapacity:vestTemplate.attributes.total_capacity||0};
    await request('/lobbies/characters/'+b.id,{data:vestBefore.data},token,'PUT');
    await page.evaluate(`window.openCharacterSheet(${b.id},'inventory');true`);
    await page.until(`document.querySelector('select[name="equipment.vest.model"]')`,'Разгрузка видна в обычном листе');
    assert.equal(await page.evaluate(`document.querySelector('.vest-pouch-name').textContent`),pouches[0].name,'Готовый подсумок подписан вместо пустого списка');
    await page.evaluate(`(()=>{const select=document.querySelector('select[name="equipment.vest.model"]');select.value='${nextVest.id}';select.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
    await page.until(`(async()=>{const {Server}=await import('/static/js/api.js');const c=await Server.getCharacter(${b.id});return c.data.equipment.vest?.templateId===${nextVest.id}&&c.data.inventory.backpack?.some(item=>item.id==='vest-preserved-bandage'&&item.quantity===4)})()`,'Смена модели сохранила подсумок в реальном сохранении');
    const afterVest=await read(b.id);
    assert.equal(afterVest.data.equipment.vest.id,'own-vest');
    assert.equal(afterVest.data.inventory.backpack.filter(item=>item.id==='vest-preserved-bandage').length,1);
    const modular=vestCatalog.global.find(template=>template.attributes?.modular&&template.attributes.discount_pouch_type==='Медицинский'&&template.attributes.total_capacity===8);
    assert.ok(modular);
    await page.evaluate(`(()=>{const select=document.querySelector('select[name="equipment.vest.model"]');select.value='${modular.id}';select.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
    await page.until(`document.querySelector('.vest-mount-usage')&&document.querySelector('button[onclick="addVestPouch()"]')`,'Каталожная модульная разгрузка позволяет добавлять подсумки');
    await page.evaluate(`document.querySelector('button[onclick="addVestPouch()"]').click();true`);
    await page.until(`document.querySelector('#pouch-select-modal select')`,'Открыт выбор подсумка из инвентаря');
    await page.evaluate(`(()=>{const modal=document.querySelector('#pouch-select-modal');modal.querySelector('select').value='0';modal.querySelector('.pouch-confirm').click();return true})()`);
    await page.until(`document.querySelector('.vest-mount-usage')?.textContent==='Объём подсумков: 2 / 8'`,'Профильная скидка 0,5 уменьшает занимаемый объём');
    assert.equal(await page.evaluate(`document.querySelector('#vest-pouches-container .container-item').dataset.widgetIdentity`),'\u007b"id":"medical-pouch-owned"\u007d');
    await page.evaluate(`document.querySelector('button[onclick="addVestPouch()"]').click();true`);
    await page.evaluate(`document.querySelector('#pouch-select-modal .pouch-confirm').click();true`);
    assert.equal(await page.evaluate(`document.querySelectorAll('#vest-pouches-container .container-item').length`),1,'Превышение объёма не добавляет подсумок');
    await page.evaluate(`document.querySelector('#pouch-select-modal .pouch-cancel').click();true`);
    await page.evaluate(`document.querySelector('.vest-mount-usage').scrollIntoView({block:'center'});true`);
    const modularShot=await page.call('Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(safeSession.artifacts,'vest-modular.png'),Buffer.from(modularShot.data,'base64'));
    await page.until(`(async()=>{const {Server}=await import('/static/js/api.js');const c=await Server.getCharacter(${b.id});return c.data.equipment.vest?.pouches?.[0]?.id==='medical-pouch-owned'&&!c.data.inventory.backpack?.some(item=>item.id==='medical-pouch-owned')})()`,'Модульный подсумок сохранён без дубликата');
    await page.evaluate(`document.querySelector('#vest-pouches-container [data-widget-key="pouch-delete"]').click();true`);
    await page.until(`(async()=>{const {Server}=await import('/static/js/api.js');const c=await Server.getCharacter(${b.id});return !c.data.equipment.vest?.pouches?.length&&c.data.inventory.backpack?.some(item=>item.id==='medical-pouch-owned')})()`,'Снятый подсумок вернулся в инвентарь');
    console.log(JSON.stringify({trade:true,items_and_money:true,money_only:true,merchant_configuration:true,merchant_net_purchase:125,filtered_selection_preserved:true,narrow_width:480}));
    console.log(JSON.stringify({npc_loot:true,filters:true,saved_profile:true,budget:80,idempotent:true}));
    console.log(JSON.stringify({vest_model_change:true,contents_preserved:true,quantity:4,normal_sheet_save:true}));
    console.log(JSON.stringify({modular_vest:true,pouch_volume_discount:.5,volume_limit_enforced:true,ready_pouch_names:true}));
    await page.evaluate(`window.closeCharacterSheet?.();true`);
    const competitor=await make('Второй покупатель',1000,[],43);
    const merchant=await read(b.id),buyer=await read(a.id),buyer2=await read(competitor.character.id);
    const stock=merchant.data.inventory.pockets.find(item=>item.id==='trade-water');
    assert.equal(stock.quantity,1);
    const beforeFunds=merchant.data.inventory.money+buyer.data.inventory.money+buyer2.data.inventory.money;
    const buyLast=(customer,placedId)=>fetch(base+loc+'/character-interactions',{
        method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`},
        body:JSON.stringify({kind:'trade',actor_location_character_id:placedId,target_character_id:merchant.id,
            payload:{merchant_mode:true,actor_revision:customer.data._revision,target_revision:merchant.data._revision,
                target_offer:[{item_id:stock.id,path:['inventory','pockets',merchant.data.inventory.pockets.indexOf(stock)],amount:1}]}}),
    }).then(async response=>({status:response.status,body:await response.json()}));
    const attempts=await Promise.all([buyLast(buyer,actorId),buyLast(buyer2,competitor.placed.location_character_id)]);
    assert.equal(attempts.filter(result=>result.status===201).length,1,JSON.stringify(attempts.map(result=>({status:result.status,error:result.body.error}))));
    assert.equal(attempts.filter(result=>result.status===409).length,1,'Второй покупатель получает понятный конфликт актуальности');
    const [afterMerchant,afterBuyer,afterBuyer2]=await Promise.all([read(b.id),read(a.id),read(competitor.character.id)]);
    assert.equal(afterMerchant.data.inventory.money,merchant.data.inventory.money+75);
    assert.equal(afterMerchant.data.inventory.money+afterBuyer.data.inventory.money+afterBuyer2.data.inventory.money,beforeFunds);
    assert.ok(!afterMerchant.data.inventory.pockets.some(item=>item.id===stock.id));
    console.log(JSON.stringify({last_stock_concurrent_buyers:true,statuses:attempts.map(result=>result.status),money_conserved:true}));
    await require('./trade_peer.browser.cjs')({page,request,token,lobby,location,actor,base,connect,debugPort,safeSession});
    const weaponCatalog=await request(api+'/templates?category=weapon',null,token,'GET');
    const repairTemplate=weaponCatalog.global.find(template=>template.price>0);
    assert.ok(repairTemplate,'Для проверки ремонта нужно оружие с каталожной ценой');
    const repairActor=await read(a.id);
    repairActor.data.inventory.money=Math.max(100000,repairTemplate.price);
    repairActor.data.weapons=[{id:'browser-repair-weapon',templateId:repairTemplate.id,category:'weapon',
        name:repairTemplate.name,durability:50,maxDurability:100}];
    await request('/lobbies/characters/'+a.id,{data:repairActor.data},token,'PUT');
    await request(loc+'/spawn_character',{character_id:a.id,tile_x:41,tile_y:48,force_relocate:true},token);
    await page.evaluate(`import('/static/js/locationScene.js').then(m=>m.showCharacterTradeMenu(${actorId},${a.id},${b.id})).then(()=>true)`);
    await page.evaluate(`document.querySelector('.trade-configure').click();true`);
    await page.until(`document.querySelector('.merchant-repair-enabled')`,'Услуги механика настраиваются');
    await page.evaluate(`(()=>{document.querySelector('.merchant-repair-enabled').checked=true;document.querySelector('.merchant-repair-markup').value='1.2';document.querySelector('.merchant-save').click();return true})()`);
    await page.until(`document.querySelector('.trade-repair')`,'У механика доступен ремонт');
    await page.evaluate(`document.querySelector('.trade-repair').click();true`);
    await page.until(`document.querySelector('.repair-item option')`,'Окно ремонта содержит оружие персонажа');
    await page.evaluate(`(()=>{const select=document.querySelector('.repair-item');const option=[...select.options].find(o=>o.textContent===${JSON.stringify(repairTemplate.name)});if(!option)throw Error('Оружие для ремонта отсутствует');select.value=option.value;select.dispatchEvent(new Event('change'));return true})()`);
    await page.evaluate(`document.querySelector('.repair-check').click();true`);
    await page.until(`!document.querySelector('.repair-confirm').disabled||document.querySelector('.trade-error').textContent`,'Сервер ответил на запрос цены ремонта');
    assert.equal(await page.evaluate(`document.querySelector('.trade-error').textContent`),'','Цена ремонта рассчитывается без ошибки');
    const repairQuote=await page.evaluate(`document.querySelector('.repair-quote').textContent`);
    assert.match(repairQuote,/Прочность после ремонта/);
    await page.evaluate(`document.querySelector('.repair-confirm').click();true`);
    await page.until(`document.querySelector('.character-trade').parentElement.style.display==='none'`,'Ремонт оплачен');
    const afterRepair=await read(a.id);
    assert.equal(afterRepair.data.weapons[0].durability,afterRepair.data.weapons[0].maxDurability);
    assert.ok(afterRepair.data.inventory.money<repairActor.data.inventory.money);
    console.log(JSON.stringify({professional_repair_ui:true,server_quote:true,money_spent:true,weapon_repaired:true}));
    afterRepair.data.inventory.money=30000;
    afterRepair.data.weapons=[{id:'browser-modification-weapon',name:'Пистолет',category:'weapon',
        subcategory:'Пистолеты',itemClass:'1',ergonomics:20,modifications:[]}];
    await request('/lobbies/characters/'+a.id,{data:afterRepair.data},token,'PUT');
    await page.evaluate(`import('/static/js/locationScene.js').then(m=>m.showCharacterTradeMenu(${actorId},${a.id},${b.id})).then(()=>true)`);
    await page.evaluate(`document.querySelector('.trade-configure').click();true`);
    await page.until(`document.querySelector('.merchant-modification-enabled')`,'Настройка услуг мастера доступна');
    await page.evaluate(`(()=>{document.querySelector('.merchant-modification-enabled').checked=true;
        document.querySelector('.merchant-repair-markup').value='1.2';document.querySelector('.merchant-save').click();return true})()`);
    await page.until(`document.querySelector('.trade-modification')`,'Взаимодействие с НПС предлагает установку');
    await page.evaluate(`document.querySelector('.trade-modification').click();true`);
    await page.until(`document.querySelector('.modification-item-card .modification-slots span')`,'Экран установки показывает предмет и слоты');
    assert.match(await page.evaluate(`document.querySelector('.modification-item-card').textContent`),/Пистолет/);
    assert.ok(await page.evaluate(`document.querySelectorAll('.modification-slots span').length>0`));
    await page.evaluate(`(()=>{const select=document.querySelector('.modification-rule');
        if(![...select.options].some(option=>option.value==='weapon-c23'))throw Error('Модификация пистолета недоступна');
        select.value='weapon-c23';select.dispatchEvent(new Event('change'));return true})()`);
    const modificationShot=await page.call('Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(safeSession.artifacts,'modification-desktop.png'),Buffer.from(modificationShot.data,'base64'));
    await page.call('Emulation.setDeviceMetricsOverride',{width:480,height:900,deviceScaleFactor:1,mobile:false});
    assert.ok(await page.evaluate(`document.querySelector('.modification-service').getBoundingClientRect().width<=480`));
    const modificationNarrow=await page.call('Page.captureScreenshot',{format:'png'});
    fs.writeFileSync(path.join(safeSession.artifacts,'modification-narrow.png'),Buffer.from(modificationNarrow.data,'base64'));
    await page.call('Emulation.clearDeviceMetricsOverride');
    await page.evaluate(`document.querySelector('.modification-check').click();true`);
    await page.until(`!document.querySelector('.modification-confirm').disabled||document.querySelector('.modification-error').textContent`,'Сервер рассчитал цену установки');
    assert.equal(await page.evaluate(`document.querySelector('.modification-error').textContent`),'');
    assert.match(await page.evaluate(`document.querySelector('.modification-price-value').textContent`),/6\s?000/);
    await page.evaluate(`document.querySelector('.modification-confirm').click();true`);
    await page.until(`document.querySelector('.modification-service').parentElement.style.display==='none'`,'Установка у НПС оплачена');
    const afterModification=await read(a.id);
    assert.equal(afterModification.data.inventory.money,24000);
    assert.ok(afterModification.data.weapons[0].modifications.some(value=>value.ruleId));
    console.log(JSON.stringify({professional_modification_ui:true,slots_visible:true,server_quote:true,money_spent:true,weapon_modified:true}));
    await page.evaluate(`import('/static/js/locationScene.js').then(m=>m.showCharacterTradeMenu(${actorId},${a.id},${b.id})).then(()=>true)`);
    await page.evaluate(`document.querySelector('.trade-modification').click();true`);
    await page.until(`document.querySelector('.modification-unlock')`,'Открытие слота доступно у механика');
    assert.equal(await page.evaluate(`document.querySelector('.modification-unlock').disabled`),false);
    await page.evaluate(`window.confirm=()=>true;document.querySelector('.modification-unlock').click();true`);
    await page.until(`document.querySelector('.modification-service').parentElement.style.display==='none'||document.querySelector('.modification-error').textContent`,'Механик обработал открытие слота');
    assert.equal(await page.evaluate(`document.querySelector('.modification-error').textContent`),'');
    const afterSlot=await read(a.id);
    assert.equal(afterSlot.data.inventory.money,21500);
    assert.equal(afterSlot.data.weapons[0].unlockedModificationSlots,1);
    assert.equal(afterSlot.data.weapons[0].modifications.length,1);
    console.log(JSON.stringify({mechanic_slot_ui:true,slot_persisted:true,exact_price:true}));
    await page.evaluate(`window.openCharacterSheet(${a.id},'equipment');true`);
    await page.until(`document.querySelector('[data-weapon-draw-hand="left"][data-weapon-draw-index="0"]')`,
        'У пистолета есть выбор левой руки');
    assert.equal(await page.evaluate(`[...document.querySelectorAll('[data-weapon-index="0"] button')]
        .some(button=>button.textContent.trim()==='Взять в руки'||button.textContent.trim().startsWith('Достать оружие'))`),false);
    await page.evaluate(`document.querySelector('[data-weapon-draw-hand="left"][data-weapon-draw-index="0"]').click();true`);
    await page.until(`document.querySelector('[data-weapon-grip-label="0"]')?.textContent.includes('левая рука')`,
        'Пистолет взят в левую руку');
    assert.equal((await read(a.id)).data.health.combatMeta.weaponPrimaryHand,'left');
    const gripEpoch=await page.evaluate('performance.timeOrigin');
    await page.call('Page.navigate',{url:href});
    await waitForDocument(page,{href,afterEpoch:gripEpoch,label:'Припять: повторный вход после выбора руки'});
    await page.until(`window.socket?.connected&&window.currentLocationId===${location.id}
        &&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,
        'Сцена вновь готова после выбора руки');
    await page.evaluate(`window.openCharacterSheet(${a.id},'equipment');true`);
    await page.until(`document.querySelector('[data-weapon-grip-label="0"]')?.textContent.includes('левая рука')`,
        'Левая рука сохранена после перезахода');
    await page.evaluate(`window.closeCharacterSheet();true`);
    console.log(JSON.stringify({pistolLeftHandUi:true,noGenericDrawButton:true,gripAfterReload:true}));
    const armorCatalog=await request(api+'/templates?category=armor',null,token,'GET');
    const armorTemplate=armorCatalog.global.find(template=>template.price>0
        && !template.attributes?.integrated_helmet);
    assert.ok(armorTemplate,'Каталожная броня для проверки инвентаря');
    const armorOwner=await read(a.id);
    armorOwner.data.inventory.pockets.push({id:'browser-armor-equip',templateId:armorTemplate.id,
        name:armorTemplate.name,category:'armor',quantity:1,weight:armorTemplate.weight,
        volume:armorTemplate.volume,modifications:[{name:'Существующая запись',description:'Сохранить'}]});
    await request('/lobbies/characters/'+a.id,{data:armorOwner.data},token,'PUT');
    await page.evaluate(`window.openCharacterSheet(${a.id},'inventory');true`);
    await page.until(`[...document.querySelectorAll('#pockets-container [data-path]')]
        .some(node=>node.textContent.includes(${JSON.stringify(armorTemplate.name)}))`,
        'Каталожная броня показана в инвентаре');
    const armorButton=await page.evaluate(`(()=>{const row=[...document.querySelectorAll('#pockets-container [data-path]')]
        .find(node=>node.textContent.includes(${JSON.stringify(armorTemplate.name)}));
        return row?.querySelector('[data-widget-key="equip"]')?.textContent})()`);
    assert.equal(armorButton,'Надеть');
    await page.evaluate(`(()=>{const row=[...document.querySelectorAll('#pockets-container [data-path]')]
        .find(node=>node.textContent.includes(${JSON.stringify(armorTemplate.name)}));
        row.querySelector('[data-widget-key="equip"]').click();return true})()`);
    await page.until(`document.querySelector('[data-equipment-group="armor"] button[onclick="unequipArmor()"]')
        &&document.querySelector('[data-equipment-group="armor"]')?.textContent.includes(${JSON.stringify(armorTemplate.name)})`,
        'Броня надета через инвентарь');
    const equippedArmor=await read(a.id);
    assert.equal(equippedArmor.data.equipment.armor.templateId,armorTemplate.id);
    assert.equal(equippedArmor.data.equipment.armor.modifications[0].name,'Существующая запись');
    assert.ok(!equippedArmor.data.inventory.pockets.some(item=>item.id==='browser-armor-equip'));
    await page.evaluate(`window.closeCharacterSheet();true`);
    const armorEpoch=await page.evaluate('performance.timeOrigin');
    await page.call('Page.navigate',{url:href});
    await waitForDocument(page,{href,afterEpoch:armorEpoch,label:'Припять: повторный вход с бронёй'});
    await page.until(`window.socket?.connected&&window.currentLocationId===${location.id}
        &&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,
        'Сцена вновь готова с надетой бронёй');
    await page.evaluate(`window.openCharacterSheet(${a.id},'equipment');true`);
    await page.until(`document.querySelector('[data-equipment-group="armor"] button[onclick="unequipArmor()"]')`,
        'Броня сохранена после перезахода');
    await page.evaluate(`window.closeCharacterSheet();true`);
    console.log(JSON.stringify({armorEquipButtonVisible:true,catalogArmorEquipped:true,
        itemMoved:true,modificationPreserved:true,armorAfterReload:true}));
    const throwOwner=await read(a.id);
    throwOwner.data.inventory.pockets.push(
        {id:'throw-light-object',name:'Лёгкий предмет для броска',category:'misc',weight:.3,quantity:1},
        {id:'throw-heavy-armor',name:'Слишком тяжёлая броня',category:'armor',weight:16,quantity:1},
        {id:'throw-grenade',name:'Граната для отдельного действия',category:'grenade',weight:.3,quantity:1},
        {id:'throw-knife',name:'Нож для отдельного действия',category:'melee_weapon',weight:.3,quantity:1},
        {id:'throw-unweighted',name:'Предмет без массы',category:'misc',quantity:1});
    await request('/lobbies/characters/'+a.id,{data:throwOwner.data},token,'PUT');
    await page.evaluate(`window.openCharacterSheet(${a.id},'inventory');true`);
    await page.until(`[...document.querySelectorAll('#pockets-container [data-path]')]
        .some(row=>row.querySelector('input[data-widget-key="name"]')?.value==='Предмет без массы')`,
        'Все проверочные предметы показаны в инвентаре');
    const throwButtons=await page.evaluate(`(()=>{const names=${JSON.stringify([
        'Лёгкий предмет для броска','Слишком тяжёлая броня','Граната для отдельного действия',
        'Нож для отдельного действия','Предмет без массы'])};
        const rows=[...document.querySelectorAll('#pockets-container [data-path]')];
        return names.map(name=>Boolean(rows.find(row=>row.querySelector('input[data-widget-key="name"]')?.value===name)
            ?.querySelector('button[title="Метнуть предмет"]')));})()`);
    assert.deepEqual(throwButtons,[true,false,false,false,false]);
    await page.evaluate(`window.closeCharacterSheet();true`);
    console.log(JSON.stringify({throwActionVisibility:true,allowedItem:true,
        hiddenHeavyArmor:true,hiddenGrenadeAndKnife:true,hiddenMissingWeight:true}));
    const helmetCatalog=await request(api+'/templates?category=helmet',null,token,'GET');
    const maskCatalog=await request(api+'/templates?category=gas_mask',null,token,'GET');
    const helmetTemplate=helmetCatalog.global.find(template=>template.name.startsWith('Шлем ')
        && !template.attributes?.integrated_visor && !template.attributes?.requires_filter);
    const maskTemplate=maskCatalog.global.find(template=>template.category==='gas_mask');
    assert.ok(helmetTemplate&&maskTemplate,'Шлем без забрала и противогаз есть в каталоге');
    const maskOwner=await read(a.id);
    for(const [id,template] of [['helmet',helmetTemplate],['mask',maskTemplate]]){
        maskOwner.data.inventory.pockets.push({id:'browser-'+id,templateId:template.id,
            name:template.name,category:template.category,quantity:1,weight:template.weight,
            volume:template.volume,attributes:template.attributes,installedModules:[]});
    }
    await request('/lobbies/characters/'+a.id,{data:maskOwner.data},token,'PUT');
    await page.evaluate(`window.openCharacterSheet(${a.id},'inventory');true`);
    for(const [template,slot] of [[helmetTemplate,'helmet'],[maskTemplate,'gasMask']]){
        await page.until(`[...document.querySelectorAll('#pockets-container [data-path]')]
            .some(row=>row.textContent.includes(${JSON.stringify(template.name)}))`,
            'Снаряжение для проверки совместимости в инвентаре');
        await page.evaluate(`(()=>{const row=[...document.querySelectorAll('#pockets-container [data-path]')]
            .find(node=>node.textContent.includes(${JSON.stringify(template.name)}));
            row.querySelector('[data-widget-key="equip"]').click();return true})()`);
        await page.until(`document.querySelector('[data-equipment-group="${slot}"] button[onclick="unequip${slot==='helmet'?'Helmet':'GasMask'}()"]')`,
            'Шлем и противогаз надеты через интерфейс');
    }
    const maskAndHelmet=await read(a.id);
    assert.equal(maskAndHelmet.data.equipment.helmet.templateId,helmetTemplate.id);
    assert.equal(maskAndHelmet.data.equipment.gasMask.templateId,maskTemplate.id);
    await page.evaluate(`window.closeCharacterSheet();true`);
    const maskEpoch=await page.evaluate('performance.timeOrigin');
    await page.call('Page.navigate',{url:href});
    await waitForDocument(page,{href,afterEpoch:maskEpoch,label:'Припять: шлем и противогаз после перезахода'});
    await page.until(`window.socket?.connected&&window.currentLocationId===${location.id}
        &&document.getElementById('scene-loading-screen').classList.contains('is-hidden')`,
        'Сцена вновь готова со шлемом и противогазом');
    const savedMaskAndHelmet=await read(a.id);
    assert.equal(savedMaskAndHelmet.data.equipment.helmet.templateId,helmetTemplate.id);
    assert.equal(savedMaskAndHelmet.data.equipment.gasMask.templateId,maskTemplate.id);
    console.log(JSON.stringify({bareHelmetWithGasMaskUi:true,compatibleAfterReload:true}));
    const mediumTemplate=armorCatalog.global.find(template=>template.attributes?.modification_category==='Средняя');
    assert.ok(mediumTemplate,'Средняя броня присутствует в каталоге');
    const branchOwner=await read(a.id);
    branchOwner.data.inventory.pockets.push({id:'browser-medium-armor',templateId:mediumTemplate.id,
        name:mediumTemplate.name,category:'armor',quantity:1,weight:mediumTemplate.weight,
        volume:mediumTemplate.volume,attributes:mediumTemplate.attributes});
    await request('/lobbies/characters/'+a.id,{data:branchOwner.data},token,'PUT');
    const branches={
        [armorTemplate.name]:['Физический','Аномальный','Удобство'],
        [mediumTemplate.name]:['Упрочнение','Физический','Аномальный','Удобство'],
    };
    await page.evaluate(`import('/static/js/locationScene.js')
        .then(module=>module.showCharacterTradeMenu(${actorId},${a.id},${b.id})).then(()=>true)`);
    await page.until(`document.querySelector('.trade-modification')`,'Платная установка доступна');
    await page.evaluate(`document.querySelector('.trade-modification').click();true`);
    await page.until(`document.querySelector('.modification-item')`,'Список предметов мастера загружен');
    for(const [name,expected] of Object.entries(branches)){
        const actual=await page.evaluate(`(()=>{const items=document.querySelector('.modification-item');
            const option=[...items.options].find(row=>row.textContent.includes(${JSON.stringify(name)}));
            if(!option)throw Error('Броня отсутствует в списке мастера');
            items.value=option.value;items.dispatchEvent(new Event('change'));
            return [...document.querySelectorAll('.modification-rule optgroup')].map(group=>group.label)})()`);
        assert.deepEqual(actual,expected);
    }
    await page.evaluate(`document.querySelector('.modification-close').click();true`);
    await page.evaluate(`document.querySelectorAll('.character-trade').forEach(editor=>editor.closest('.modal')?.remove());true`);
    await page.evaluate(`import('/static/js/crafting.js')
        .then(module=>module.showCrafting({characterId:${a.id},lobbyId:${lobby.id}})).then(()=>true)`);
    assert.ok(await page.evaluate(`(()=>{
        const tabs=[...document.querySelectorAll('#crafting-modal [role=tab]')];
        const cards=document.querySelectorAll('#crafting-modal .crafting-recipe-card');
        const categories=document.querySelectorAll('#crafting-modal .crafting-categories button');
        return tabs.length===3&&cards.length>0&&categories.length>1&&
            document.querySelector('[data-crafting-pane=modifications]').hidden;
    })()`));
    const catalogWidth=await page.evaluate(`(()=>{const panel=document.querySelector('#crafting-modal .modal-content');
        return {scroll:panel.scrollWidth,visible:panel.clientWidth}})()`);
    assert.ok(catalogWidth.scroll<=catalogWidth.visible+2,JSON.stringify(catalogWidth));
    fs.writeFileSync(path.join(safeSession.artifacts,'crafting-catalog.png'),
        Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    await page.call('Emulation.setDeviceMetricsOverride',{width:480,height:800,deviceScaleFactor:1,mobile:false});
    const mobileCatalog=await page.evaluate(`(()=>{const panel=document.querySelector('#crafting-modal .modal-content');
        return {panel:panel.getBoundingClientRect().width,viewport:innerWidth,scroll:panel.scrollWidth,visible:panel.clientWidth}})()`);
    assert.ok(mobileCatalog.panel<=mobileCatalog.viewport&&mobileCatalog.scroll<=mobileCatalog.visible+2,JSON.stringify(mobileCatalog));
    fs.writeFileSync(path.join(safeSession.artifacts,'crafting-catalog-mobile.png'),
        Buffer.from((await page.call('Page.captureScreenshot',{format:'png'})).data,'base64'));
    await page.call('Emulation.clearDeviceMetricsOverride');
    await page.evaluate(`document.querySelector('[data-crafting-tab=modifications]').click();true`);
    assert.ok(await page.evaluate(`!document.querySelector('[data-crafting-pane=modifications]').hidden &&
        document.querySelector('[data-crafting-pane=creation]').hidden`));
    await page.until(`document.querySelector('#crafting-modal select[aria-label="Предмет для модификации"]')`,
        'Самостоятельная установка доступна');
    for(const [name,expected] of Object.entries(branches)){
        const actual=await page.evaluate(`(()=>{const items=document.querySelector('#crafting-modal select[aria-label="Предмет для модификации"]');
            const option=[...items.options].find(row=>row.textContent.includes(${JSON.stringify(name)}));
            if(!option)throw Error('Броня отсутствует в самостоятельной установке');
            items.value=option.value;items.dispatchEvent(new Event('change'));
            return [...document.querySelectorAll('#crafting-modal select[aria-label="Модификация"] optgroup')]
                .map(group=>group.label)})()`);
        assert.deepEqual(actual,expected);
    }
    await page.evaluate(`document.querySelector('[data-crafting-tab=creation]').click();
        const recipes=document.querySelector('#crafting-modal select[aria-label=Рецепт]');
        recipes.value='recipe-v26';recipes.dispatchEvent(new Event('change'));true`);
    await page.until(`document.querySelector('#crafting-modal select[aria-label="Калибр патронов"]')`,
        'Конкретные патроны доступны в рецепте');
    const ammoUi=await page.evaluate(`(()=>{
        const ammo=document.querySelector('#crafting-modal select[aria-label="Калибр патронов"]');
        const selected=[...ammo.options].find(option=>option.textContent.includes('5.45x39'));
        if(!selected)return false;
        ammo.value=selected.value;ammo.dispatchEvent(new Event('change'));
        const variants=document.querySelector('#crafting-modal select[aria-label="Вид патронов"]');
        return ['base','bp','ep'].every(value=>[...variants.options].some(option=>option.value===value));
    })()`);
    assert.ok(ammoUi,'Рецепт категории 2 предлагает 5.45x39, БП и ЭП');
    await page.evaluate(`document.querySelector('#crafting-modal .block-header button').click();true`);
    console.log(JSON.stringify({armorBranchesGrouped:true,families:['Лёгкая','Средняя'],
        selfCrafting:true,professionalInstallation:true}));
    await page.evaluate(`(async()=>{const generator=await import('/static/js/npcGenerator.js');
        await generator.openNpcCreation(${lobby.id});return true})()`);
    await page.until(`document.querySelector('.npc-generator-name')?.value.includes(' ')
        &&!document.querySelector('.npc-generator-reroll').disabled`,'авторское имя НПС предложено');
    const firstName=await page.evaluate(`document.querySelector('.npc-generator-name').value`);
    await page.evaluate(`document.querySelector('.npc-generator-reroll').click();true`);
    await page.until(`document.querySelector('.npc-generator-name')?.value!==${JSON.stringify(firstName)}`,
        'авторское имя НПС переброшено');
    const generatedName=await page.evaluate(`document.querySelector('.npc-generator-name').value`);
    await page.evaluate(`document.querySelector('.npc-generator-create').click();true`);
    await page.until(`!document.querySelector('.npc-generator-name')`,'НПС с авторским именем создан');
    const generatedCharacters=await request(`/lobbies/${lobby.id}/characters`,null,token,'GET');
    assert.ok(generatedCharacters.some(character=>character.name===generatedName
        &&character.data?.npcGenerator?.name_source==='author_name_parts'));
    console.log(JSON.stringify({author_npc_name_ui:true,reroll:true,created:true}));
    await require('./world_factions_ready.browser.cjs')({page,request,token,safeSession});
};
