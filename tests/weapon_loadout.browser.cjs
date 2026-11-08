// Проверка настоящего листа в отдельной БД; игровые данные пользователя не используются.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
module.exports=async({page,request,token,lobby,safeSession})=>{
    const create=body=>request(`/lobbies/${lobby.id}/templates`,body,token);
    const gun=await create({name:'АС 19-Б10 — проверка экземпляра',category:'weapon',subcategory:'Штурмовые винтовки и карабины',weight:4,volume:44750,
        attributes:{size:44750,import_source:'equipment_workbook',accuracy:1,noise:15,caliber:'9x39',range:50,ergonomics:50,max_durability:100}});
    assert.equal(gun.volume,8);assert.deepEqual(gun.attributes.size_options,[8,7]);
    const launcher=await create({name:'Гранатомёт — без стволового слота',category:'weapon',subcategory:'Гранатометы',volume:10,attributes:{caliber:'ВОГ-25'}});
    const definitions=[
        {name:'Оптика',slot:'scope',modifiers:{range:15,ergonomics:-15}},
        {name:'Глушитель',slot:'barrel',modifiers:{noise:'=2',ergonomics:-15},caliber:'9 * 39'},
        {name:'ГП-25',slot:'handguard',modifiers:{ergonomics:-30},type:'grenade_launcher',compatible_weapon_categories:['Штурмовые винтовки и карабины']},
    ];
    const modules=[];
    for(const [i,d]of definitions.entries()){
        const template=await create({name:d.name,category:'weapon_module',subcategory:d.slot,weight:.5,volume:.2,
            attributes:{...d,slot_type:d.slot}});
        modules.push({id:`owned-module-${i}`,templateId:template.id+1000000,name:d.name,category:'weapon_module',weight:.5,volume:.2,quantity:i===0?2:1,
            durability:73,attributes:template.attributes,...(i===2?{loaded:true,loadedGrenade:{id:'smoke-round',name:'ВОГ-25 дым',category:'ammo',quantity:1,attributes:{ammo_variant:'smoke'}}}:{} )});
    }
    const character=await request(`/lobbies/${lobby.id}/characters`,{name:'Проверка оружия и модулей',data:{
        weapons:[],inventory:{backpack:modules,pockets:[]},health:{effects:[]},skills:{physical:{strength:{base:10}}},
    }},token);
    const read=()=>request(`/lobbies/characters/${character.id}`,null,token,'GET');
    const click=async selector=>{
        await page.until(`document.querySelector(${JSON.stringify(selector)})?.getClientRects().length`,'элемент листа виден');
        let point;
        for(let attempt=0;attempt<30&&!point;attempt++){
            point=await page.evaluate(`(async()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)return null;e.scrollIntoView({block:'center',behavior:'instant'});await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));const r=e.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;return e.isConnected&&e.contains(document.elementFromPoint(x,y))?{x,y}:null})()`);
            if(!point)await pause(100);
        }
        assert.ok(point,'Кнопка доступна после обновления листа: '+selector);
        for(const type of ['mousePressed','mouseReleased'])await page.call('Input.dispatchMouseEvent',{type,...point,button:'left',buttons:type==='mousePressed'?1:0,clickCount:1});
    };
    const set=async(selector,value)=>page.evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});e.value=${JSON.stringify(String(value))};e.dispatchEvent(new Event('change',{bubbles:true}))})()`);
    const waitSaved=async predicate=>{
        for(let i=0;i<100;i++){const s=await read();if(predicate(s.data))return s.data;await pause(100);}
        throw Error('Ожидаемое состояние оружия не сохранено');
    };
    await page.evaluate(`window.openCharacterSheet(${character.id},'equipment')`);
    await page.until('document.querySelector("#sheet-tab-equipment .equipment-panel-nav .equipment-panel-tab[data-panel=weapons]") && document.querySelector("#weapons-container")','лист экипировки загружен');
    await click('#sheet-tab-equipment .equipment-panel-tab[data-panel="weapons"]');
    await click('#sheet-tab-equipment [onclick="addWeapon()"]');
    await page.until('document.getElementById("weapon-model-select-0")','выбор модели');
    await set('#weapon-model-select-0',gun.id+1000000);
    // Дожидаемся также обновления после автосохранения добавленного пустого оружия.
    await waitSaved(d=>d.weapons?.length===1);await pause(1200);
    assert.equal(await page.evaluate('document.getElementById("weapon-model-select-0").value'),String(gun.id+1000000),'Выбор пережил автосохранение');
    const remote=(await read()).data;
    remote.basic={...(remote.basic||{}),tags:['Проверка синхронизации']};
    remote.weapons[0].customNote='Внешнее обновление оружия';
    await request(`/lobbies/characters/${character.id}`,{data:remote},token,'PUT');
    await page.until('document.querySelector("#sheet-tab-basic")?.textContent.includes("Проверка синхронизации")','внешнее обновление доставлено в лист');
    await page.until(`document.getElementById("weapon-model-select-0")?.value===${JSON.stringify(String(gun.id+1000000))}`,'незавершённый выбор восстановлен после внешнего обновления');
    assert.equal(await page.evaluate('document.getElementById("weapon-model-select-0").value'),String(gun.id+1000000),'Выбор пережил внешнее обновление');
    await click('[onclick="selectWeaponModel(0)"]');
    await waitSaved(d=>d.weapons[0]?.templateId===gun.id+1000000);
    const openStats=async()=>{
        await click('#sheet-tab-equipment .equipment-panel-tab[data-panel="weapons"]');
        for(const selector of ['[data-equipment-panel="weapon-0"]',
            '#weapons-container [data-weapon-index="0"] details']){
            await page.until(`document.querySelector(${JSON.stringify(selector)})`,'панель оружия загружена');
            if(!await page.evaluate(`document.querySelector(${JSON.stringify(selector)}).open`))await click(selector+' > summary');
        }
    };
    await openStats();
    for(const d of definitions){
        const button=`[onclick="equipModuleToWeapon(0, '${d.slot}')"]`;
        await click(button);
        await page.until('document.querySelector("#module-select-modal #confirm-module-btn")','меню установки модуля');
        await click('#confirm-module-btn');
        await waitSaved(data=>data.weapons[0]?.installedModules?.some(m=>m.slotType===d.slot));
    }
    const installed=await read(),weapon=installed.data.weapons[0];
    assert.equal(weapon.installedModules.length,3);
    assert.equal(installed.data.inventory.backpack.find(i=>i.id==='owned-module-0').quantity,1,'Установка берёт один модуль из стопки');
    assert.equal(weapon.installedModules.find(m=>m.slotType==='handguard').loadedGrenade.id,'smoke-round');
    await click('[onclick="unequipWeapon(0)"]');
    const removed=await waitSaved(d=>d.weapons.length===0&&d.inventory.backpack.some(i=>i.id===weapon.id));
    let item=removed.inventory.backpack.find(i=>i.id===weapon.id);
    assert.equal(item.volume,8);assert.deepEqual(item.installedModules,weapon.installedModules);
    const itemIndex=removed.inventory.backpack.findIndex(i=>i.id===weapon.id);
    await page.evaluate(`window.equipWeaponFromInventory(['inventory','backpack',${itemIndex}])`);
    const equipped=await waitSaved(d=>d.weapons[0]?.id===weapon.id);
    assert.deepEqual(equipped.weapons[0].installedModules,weapon.installedModules);
    const epoch=await page.evaluate('performance.timeOrigin');
    await page.call('Page.reload');
    await page.until(`performance.timeOrigin>${epoch}&&Boolean(window.openCharacterSheet)`,'новый документ загрузил лист персонажа');
    await page.evaluate(`window.openCharacterSheet(${character.id},'equipment')`);
    await page.until(`document.querySelector('#sheet-tab-equipment .equipment-panel-nav .equipment-panel-tab[data-panel="weapons"]') && document.querySelector('#weapons-container [data-weapon-index="0"]')`,'лист оружия загружен после перезагрузки');
    await openStats();
    const reloaded=await read();assert.deepEqual(reloaded.data.weapons[0].installedModules,weapon.installedModules);
    await click('[onclick="unequipModuleFromWeapon(0, \'handguard\')"]');
    const detached=await waitSaved(d=>d.weapons[0]?.installedModules?.length===2);
    const returned=detached.inventory.backpack.find(i=>i.id==='owned-module-2');
    assert.equal(returned.loadedGrenade.id,'smoke-round');assert.equal(returned.loaded,true);assert.equal(returned.durability,73);assert.equal(returned.weight,.5);
    // У гранатомёта тот же интерфейс показывает только два разрешённых слота.
    await click('#sheet-tab-equipment [onclick="addWeapon()"]');
    await page.until('document.getElementById("weapon-model-select-1")','вторая модель');
    assert.deepEqual(await page.evaluate(`[...document.querySelectorAll('.loadout-weapon-details[open]')].map(p=>p.dataset.equipmentPanel)`),
        ['weapon-1'],'Новое оружие раскрыто сразу, остальные карточки свёрнуты');
    await set('#weapon-model-select-1',launcher.id+1000000);await click('[onclick="selectWeaponModel(1)"]');
    await waitSaved(d=>d.weapons[1]?.templateId===launcher.id+1000000);
    assert.equal(await page.evaluate('Boolean(document.querySelector(`[onclick="equipModuleToWeapon(1, \'barrel\')"]`))'),false);
    // Проверяем установку каждого встроенного модуля обычными кнопками листа.
    const catalog=(await request(`/lobbies/${lobby.id}/templates?category=weapon_module`,null,token,'GET')).global.filter(t=>t.attributes?.moduleRuleId);
    assert.equal(catalog.length,12);
    const baseline=(await read()).data;
    baseline.weapons=[{...weapon,installedModules:[]}];
    baseline.inventory.backpack=catalog.map((t,i)=>({...t,id:`catalog-module-${i}`,templateId:t.id,quantity:1,contents:[],installedModules:[]}));
    await request(`/lobbies/characters/${character.id}`,{data:baseline},token,'PUT');
    await page.until(`document.querySelector('#weapons-container [data-weapon-index="0"]')&&!document.querySelector('#weapons-container [data-weapon-index="1"]')`,'оружие для проверки полного каталога');
    const checked=[];
    const ordered=[...catalog].sort((a,b)=>Number(a.attributes.removable===false)-Number(b.attributes.removable===false));
    for(const t of ordered){
        await openStats();
        await click(`[onclick="equipModuleToWeapon(0, '${t.subcategory}')"]`);
        await page.until('document.querySelector("#module-select")','список модулей');
        await page.evaluate(`(()=>{const select=document.querySelector('#module-select');const option=[...select.options].find(o=>o.textContent.startsWith(${JSON.stringify(t.name+' (')}));if(!option)throw Error('Модуль не найден');select.value=option.value})()`);
        await click('#confirm-module-btn');
        let saved=await waitSaved(d=>d.weapons[0]?.installedModules?.some(m=>m.attributes?.moduleRuleId===t.attributes.moduleRuleId));
        const attached=saved.weapons[0].installedModules.find(m=>m.attributes.moduleRuleId===t.attributes.moduleRuleId);
        assert.equal(attached.weight,.5);assert.equal(attached.volume,1);
        if(t.attributes.caliber_specific)assert.equal(attached.attributes.caliber,'9x39');
        checked.push(t.name);
        if(t.attributes.removable===false){
            assert.equal(saved.weapons[0].volume,6,'укорочение уменьшило размер на 2');
            assert.equal(await page.evaluate(`Boolean(document.querySelector(${JSON.stringify('[onclick="unequipModuleFromWeapon(0, \'barrel\')"]')}))`),false,'несъёмный модуль не предлагает снятие');
        }else{
            await click(`[onclick="unequipModuleFromWeapon(0, '${t.subcategory}')"]`);
            saved=await waitSaved(d=>!d.weapons[0]?.installedModules?.length);
            assert.equal(saved.inventory.backpack.filter(m=>m.attributes?.moduleRuleId===t.attributes.moduleRuleId).length,1,'модуль возвращается без дубля');
        }
    }
    await openStats();
    await page.evaluate('window.confirm=()=>true');
    await click('[onclick="shortenWeaponBarrel(0)"]');
    const shortened=await waitSaved(d=>d.weapons[0]?.installedModules?.some(m=>m.attributes?.moduleRuleId==='short_barrel'));
    assert.equal(shortened.weapons[0].volume,6,'действие уменьшает размер оружия на 2');
    assert.equal(shortened.weapons[0].installedModules.length,1,'действие записывает изменение в экземпляр');
    assert.equal(await page.evaluate('Boolean(document.querySelector(`[onclick="unequipModuleFromWeapon(0, \'barrel\')"]`))'),false);
    await page.evaluate('window.shortenWeaponBarrel(0)');
    assert.equal((await read()).data.weapons[0].installedModules.length,1,'повторное действие не дублирует изменение');
    const picker=await page.evaluate(`(async()=>{
        const {renderInventoryTemplatePicker}=await import('/static/js/characterSheet.js');
        const samples=[
            ['weapon','Пистолеты'],['weapon','Дробовики'],
            ['armor','Лёгкая'],['armor','Средняя'],['armor','Тяжёлая'],
            ['helmet','Текстиль',{soft_headwear:true}],['helmet','Плита',{}],
            ['helmet','Композит',{requires_filter:true}],['helmet','Кевлар',{integrated_visor:true}],
            ['vest',''],['belt',''],['armor_plate',''],
            ['device','battery'],['exoskeleton_module','battery'],
            ['device','flamethrower_tank',{flamethrower_tank:'jet'}],
            ['device','flamethrower_air',{flamethrower_air:true}],
            ['artifact','',{anomaly_type:'Электрическая'}],
        ].map(([category,subcategory,attributes],id)=>({id:id+1,name:'Образец '+id,category,subcategory,
            attributes:attributes||{},item_class:category==='artifact'?'2':null}));
        const root=document.createElement('div');root.innerHTML=renderInventoryTemplatePicker(samples);
        const roots=[...root.querySelectorAll(':scope > details')];
        const section=name=>{
            const root=roots.find(item=>item.querySelector(':scope > summary span')?.textContent===name);
            return root?[...root.querySelectorAll(':scope > details > summary span:first-child')].map(item=>item.textContent):[];
        };
        return {weapons:section('Оружие'),armor:section('Броня'),helmets:section('Шлемы'),
            vests:section('Разгрузки'),devices:section('Приборы'),
            artifacts:section('Артефакты'),
            duplicateIds:(()=>{const ids=[...root.querySelectorAll('[data-item-template-id]')].map(item=>item.dataset.itemTemplateId);return ids.length-new Set(ids).size})()};
    })()`);
    assert.ok(picker.weapons.includes('Пистолеты')&&picker.weapons.includes('Дробовики'),JSON.stringify(picker));
    assert.ok(picker.armor.includes('Лёгкая')&&picker.armor.includes('Средняя')&&picker.armor.includes('Тяжёлая'));
    assert.ok(picker.helmets.includes('Головные уборы')&&picker.helmets.includes('Шлемы-противогазы'));
    assert.ok(picker.vests.includes('Пояса')&&picker.vests.includes('Бронеплиты'));
    assert.ok(picker.devices.includes('Батарейки')&&picker.devices.includes('Аккумуляторы экзоскелета'));
    assert.ok(picker.devices.includes('Баки и Баллоны Огнеметов'));
    assert.ok(!picker.devices.includes('Другие предметы'));
    assert.ok(picker.artifacts.includes('Электрическая'));
    assert.equal(picker.duplicateIds,0,'группировка не дублирует шаблоны');
    fs.writeFileSync(path.join(safeSession.artifacts,'weapon-loadout.json'),JSON.stringify({modelSurvivesSave:true,threeSlots:true,launcherTwoSlots:true,
        oneModuleFromStack:true,weaponIdPreserved:true,volume:8,loadedLauncherPreserved:true,reloadPreserved:true,detachedModuleStatePreserved:true,allCatalogModules:checked,shortBarrelVolume:6},null,2));
};
